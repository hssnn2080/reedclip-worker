import os
import subprocess
from typing import List, Tuple, Iterator
import ffmpeg
from PIL import Image, ImageDraw, ImageFont



import glob

def download_media(audio_url: str, video_url: str, output_dir: str, video_sections: List[str] = None) -> Tuple[str, List[str]]:
    """Downloads audio and video to the specified directory."""
    audio_path = os.path.join(output_dir, "input_audio.m4a")

    print(f"Downloading audio from {audio_url}...")
    subprocess.run([
        "yt-dlp", "--no-playlist", "-f", "bestaudio[ext=m4a]/bestaudio", 
        "-o", audio_path, audio_url
    ], check=True, stdin=subprocess.DEVNULL)

    print(f"Downloading video from {video_url}...")
    cmd = ["yt-dlp", "--no-playlist", "-f", "bestvideo[height>=1080][ext=mp4]/bestvideo/best", "--merge-output-format", "mp4"]
    
    if video_sections:
        for sec in video_sections:
            cmd.extend(["--download-sections", sec])
        video_output_template = os.path.join(output_dir, "input_video_%(autonumber)s.mp4")
        cmd.extend(["-o", video_output_template, video_url])
        subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)
        
        video_paths = sorted(glob.glob(os.path.join(output_dir, "input_video_*.mp4")))
        return audio_path, video_paths
    else:
        video_path = os.path.join(output_dir, "input_video.mp4")
        cmd.extend(["-o", video_path, video_url])
        subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)
        return audio_path, [video_path]

def get_fixed_cuts(audio_dur_ms: int, clip_duration_s: int) -> List[Tuple[int, int]]:
    """Generates fixed duration cut points."""
    chunk_ms = clip_duration_s * 1000
    chunks = []
    start_ms = 0
    while start_ms < audio_dur_ms:
        end_ms = min(start_ms + chunk_ms, audio_dur_ms)
        # Only add chunk if it's at least 3 seconds long to avoid tiny clips at the end
        if end_ms - start_ms > 3000:
            chunks.append((start_ms, end_ms))
        start_ms += chunk_ms
    print(f"Generated {len(chunks)} fixed clips ({clip_duration_s}s each).")
    return chunks

def process_clip(
    video_path: str, 
    audio_path: str, 
    audio_start_s: float, 
    duration_s: float, 
    output_path: str, 
    video_start_s: float = 0,
    logo_path: str = "logo.png",
    loop_video: bool = False
):
    """Processes a single clip."""
    if loop_video:
        vid = ffmpeg.input(video_path, stream_loop=-1, ss=video_start_s, t=duration_s)
    else:
        vid = ffmpeg.input(video_path, ss=video_start_s, t=duration_s)
        
    aud = ffmpeg.input(audio_path, ss=audio_start_s, t=duration_s)
    
    # Bulletproof aspect ratio scaling (works for both horizontal and vertical videos safely)
    v = vid.video.filter('scale', 1080, 1920, force_original_aspect_ratio='increase').filter('crop', 1080, 1920)
    
    # Overlay the reed2.png logo on the bottom left, moved up by approx 2 inches
    logo_path = "reed2.png"
    if os.path.exists(logo_path):
        logo = ffmpeg.input(logo_path).filter('scale', 150, -1)
        v = ffmpeg.overlay(v, logo, x=40, y='H-h-540')
    
    # Force pixel format to yuv420p after overlay to ensure compatibility with h264_nvenc
    v = v.filter('format', 'yuv420p')
    
    # Audio fade duration set to 4.0s but capped for very short clips
    fade_dur = min(4.0, duration_s / 3.0)
    
    a = aud.audio.filter('afade', type='in', start_time=0, duration=fade_dur)
    a = a.filter('afade', type='out', start_time=duration_s - fade_dur, duration=fade_dur)
    # Reverted to NVENC hardware encoding
    out = ffmpeg.output(v, a, output_path, vcodec='h264_nvenc', bf=0, movflags='faststart', **{'profile:v': 'main', 'b:v': '3000k', 'maxrate': '3500k', 'bufsize': '3500k', 'b:a': '128k'})
    
    print(f"Running ffmpeg for clip {output_path} (HARDWARE NVENC ONLY)...")
    ffmpeg.run(out, overwrite_output=True, capture_stdout=True, capture_stderr=True)

def generate_clips(
    audio_url: str, 
    video_url: str, 
    output_dir: str, 
    video_skip_minutes: int = 1,
    audio_skip_minutes: int = 0,
    clip_duration: int = 30,
    audio_meta: dict = None,
    video_meta: dict = None
) -> Iterator[str]:
    """Main pipeline to generate all clips."""
    os.makedirs(output_dir, exist_ok=True)
    
    import json
    if not audio_meta:
        try:
            res = subprocess.run(["yt-dlp", "--dump-json", audio_url], capture_output=True, text=True)
            audio_meta = json.loads(res.stdout)
        except:
            pass
    if not video_meta:
        try:
            res = subprocess.run(["yt-dlp", "--dump-json", video_url], capture_output=True, text=True)
            video_meta = json.loads(res.stdout)
        except:
            pass
            
    audio_dur_ms = int(float(audio_meta.get('duration', 60)) * 1000) if audio_meta else 60000
    video_dur_s = float(video_meta.get('duration', 3600)) if video_meta else 3600.0
    
    actual_audio_skip_ms = audio_skip_minutes * 60 * 1000
    if audio_dur_ms > actual_audio_skip_ms + 10000: # Ensure at least 10s of audio left
        audio_dur_ms -= actual_audio_skip_ms
    else:
        actual_audio_skip_ms = 0
        print("Audio too short to skip. Ignoring audio skip.")
        
    cut_points = get_fixed_cuts(audio_dur_ms, clip_duration)
    num_clips = len(cut_points)
    
    video_skip_s = video_skip_minutes * 60
    
    loop_video = False
    if video_dur_s < (audio_dur_ms / 1000.0):
        print("Video is shorter than audio! Enabling infinite loop mode.")
        loop_video = True
        
    actual_skip_s = 0
    video_sections = []
    
    if loop_video or video_dur_s <= video_skip_s + clip_duration:
        print("Video starting from 0 (either looping or too short to skip).")
        actual_skip_s = 0
    else:
        # We need num_clips chunks of clip_duration.
        # Let's space them evenly between video_skip_s and video_dur_s
        usable_duration = video_dur_s - video_skip_s
        interval = max(0, (usable_duration - clip_duration) / max(1, num_clips))
        actual_skip_s = video_skip_s
        
        for i in range(num_clips):
            start_s = video_skip_s + (i * interval)
            end_s = start_s + clip_duration
            
            def fmt(s):
                h = int(s // 3600)
                m = int((s % 3600) // 60)
                sec = int(s % 60)
                return f"{h:02d}:{m:02d}:{sec:02d}"
                
            video_sections.append(f"*{fmt(start_s)}-{fmt(end_s)}")
            
    # If sections are populated, we only download those specific pieces!
    audio_path, video_paths = download_media(audio_url, video_url, output_dir, video_sections)
    
    for i, (start_ms, end_ms) in enumerate(cut_points):
        audio_start_s = (start_ms + actual_audio_skip_ms) / 1000.0
        duration_s = (end_ms - start_ms) / 1000.0
        
        if loop_video or not video_sections:
            video_start_s = actual_skip_s + (start_ms / 1000.0)
            if not loop_video and (video_start_s + duration_s > video_dur_s):
                print(f"Warning: Video is too short to cover audio clip {i+1}. Stopping.")
                break
            current_video = video_paths[0]
        else:
            # We downloaded separate chunks!
            video_start_s = 0
            if i < len(video_paths):
                current_video = video_paths[i]
            else:
                current_video = video_paths[-1]
            
        clip_name = f"clip_{i+1}.mp4"
        clip_path = os.path.join(output_dir, clip_name)
        process_clip(current_video, audio_path, audio_start_s, duration_s, clip_path, video_start_s, loop_video=loop_video)
        print(f"Generated {clip_name} ({duration_s:.1f}s)")
        yield clip_path
