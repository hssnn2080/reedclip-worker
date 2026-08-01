import os, shutil
from fastapi import FastAPI, BackgroundTasks, Request
import uvicorn
import requests
import clip_processor
import boto3

import threading
import time

app = FastAPI()

processing = False
auth_key = os.environ.get('R2_ACCESS_KEY_ID', '')

def process_queue():
    global processing
    while True:
        try:
            if processing:
                time.sleep(5)
                continue
                
            res = requests.get('https://www.reedclip.com/api/queue/pop', headers={'Authorization': f'Bearer {auth_key}'})
            if not res.ok:
                time.sleep(5)
                continue
                
            data = res.json()
            if data.get('status') != 'success':
                time.sleep(5)
                continue
                
            job = data.get('job')
            if not job:
                continue
                
            processing = True
            job_id = job.get("jobId")
            
            try:
                out_dir = f"/tmp/{job_id}"
                os.makedirs(out_dir, exist_ok=True)
                
                print(f"Starting processing for {job_id}")
                clips = list(clip_processor.generate_clips(
                    audio_url=job.get('audioUrl'),
                    video_url=job.get('videoUrl'),
                    output_dir=out_dir,
                    clip_duration=int(job.get('clipDuration') or 30),
                    audio_skip_minutes=int(job.get('audioSkipMinutes') or 0),
                    video_skip_minutes=int(job.get('videoSkipMinutes') or 1)
                ))
                
                if clips:
                    final_clip = clips[0]
                    print(f"Generated clip at {final_clip}, uploading to R2...")
                    s3 = boto3.client('s3', 
                        endpoint_url="https://a24f98f842e36f14eafe06a5b0f27b91.r2.cloudflarestorage.com", 
                        aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'], 
                        aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY']
                    )
                    s3.upload_file(final_clip, 'reedclip', f"{job_id}.mp4")
                    final_video_url = os.environ['R2_PUBLIC_URL'] + f"/{job_id}.mp4"
                    
                    print(f"Pinging webhook for {job_id} with url {final_video_url}")
                    requests.post('https://www.reedclip.com/api/webhook/vast', json={'jobId': job_id, 'videoUrl': final_video_url})
                    print(f"Job {job_id} finished successfully.")
            except Exception as e:
                print(f"Job {job_id} failed: {e}")
                try:
                    requests.post('https://www.reedclip.com/api/webhook/vast', json={'jobId': job_id, 'error': str(e)})
                except:
                    pass
            finally:
                shutil.rmtree(out_dir, ignore_errors=True)
                processing = False
                
        except Exception as e:
            print("Queue polling error:", e)
            time.sleep(5)

@app.on_event("startup")
def startup_event():
    threading.Thread(target=process_queue, daemon=True).start()

@app.get("/")
def health_root():
    return {"status": "ok"}

@app.get("/status")
def health_status():
    return {"status": "ok"}

@app.get("/api/status")
def health_api_status():
    return {"status": "ok"}

@app.post("/generate")
def generate(request: Request):
    return {"status": "ok"}

if __name__ == "__main__":
    print("Application startup complete. Starting polling queue...", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=18000)
