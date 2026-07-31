import os, shutil
from fastapi import FastAPI, BackgroundTasks, Request
import uvicorn
import requests
import clip_processor
import boto3

app = FastAPI()

def process_job(payload):
    job_id = payload.get("jobId")
    audio_url = payload.get("audioUrl")
    video_url = payload.get("videoUrl")
    clip_duration = int(payload.get("clipDuration", 30))
    audio_skip = int(payload.get("audioSkipMinutes", 0))
    video_skip = int(payload.get("videoSkipMinutes", 1))
    
    out_dir = f"/tmp/{job_id}"
    os.makedirs(out_dir, exist_ok=True)
    
    try:
        print(f"Starting processing for {job_id}")
        clips = list(clip_processor.generate_clips(
            audio_url=audio_url,
            video_url=video_url,
            output_dir=out_dir,
            clip_duration=clip_duration,
            audio_skip_minutes=audio_skip,
            video_skip_minutes=video_skip
        ))
        
        if clips:
            final_clip = clips[0]
            print(f"Generated clip at {final_clip}, uploading to R2...")
            # Upload to R2
            s3 = boto3.client('s3', 
                endpoint_url=os.environ['R2_ENDPOINT'], 
                aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'], 
                aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY']
            )
            s3.upload_file(final_clip, 'reedclip', f"{job_id}.mp4")
            final_video_url = os.environ['R2_PUBLIC_URL'] + f"/{job_id}.mp4"
            
            # Ping webhook to mark as success
            print(f"Pinging webhook for {job_id} with url {final_video_url}")
            requests.post('https://reedclip.com/api/webhook/vast', json={'jobId': job_id, 'videoUrl': final_video_url})
            print(f"Job {job_id} finished successfully.")
    except Exception as e:
        print(f"Job {job_id} failed: {e}")
        try:
            requests.post('https://reedclip.com/api/webhook/vast', json={'jobId': job_id, 'error': str(e)})
        except:
            pass
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

@app.post("/generate")
async def generate(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    background_tasks.add_task(process_job, payload)
    return {"status": "ok"}

if __name__ == "__main__":
    print("Application startup complete.")
    uvicorn.run(app, host="127.0.0.1", port=18000)
