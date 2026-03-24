import os
import subprocess
import time
import json
import re
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename
import yt_dlp
import google.generativeai as genai
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import cloudinary
import cloudinary.uploader

app = Flask(__name__)

# --- الإعدادات ---
UPLOAD_FOLDER = 'static/uploads'
CLIPS_FOLDER = os.path.join(UPLOAD_FOLDER, "clips")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CLIPS_FOLDER, exist_ok=True)

# إعدادات Cloudinary
cloudinary.config( 
  cloud_name = "dbyfv4jwh", 
  api_key = "771897553861346", 
  api_secret = "L0b_NX2DJVG3MELHJYvGJm-eG_w",
  secure = True
)

# إعدادات Gemini AI
GEMINI_API_KEY = 'AIzaSyA2KpuB4kAF9SiX9qdLeVmKVKKKdGngirk'
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# --- وظائف المساعدة ---

def download_youtube_video(url):
    ydl_opts = {
        'format': 'best',
        'outtmpl': os.path.join(UPLOAD_FOLDER, '%(id)s.%(ext)s'),
        'nocheckcertificate': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)

def get_best_moments(video_path):
    prompt = "أنا أقوم بتقسيم فيديو إلى مقاطع TikTok. اقترح لي 3 لحظات مثيرة (بداية ونهاية بالثواني) لفيديو مدته غير معروفة، افترض أنه طويل. أجب بتنسيق JSON فقط: [{'start': 10, 'end': 40}, ...]"
    try:
        response = model.generate_content(prompt)
        match = re.search(r'\[.*\]', response.text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except:
        pass
    return [{'start': 30, 'end': 60}, {'start': 120, 'end': 150}]

def process_video_to_tiktok(input_path, output_path, start_time, end_time):
    duration = end_time - start_time
    cmd = (
        f'ffmpeg -ss {start_time} -t {duration} -i "{input_path}" '
        f'-vf "crop=ih*(9/16):ih,scale=1080:1920" '
        f'-c:v libx264 -crf 23 -preset veryfast -c:a copy "{output_path}" -y'
    )
    subprocess.call(cmd, shell=True)

def cleanup_old_files():
    print("Running cleanup task...")
    now = time.time()
    for f in os.listdir(CLIPS_FOLDER):
        f_path = os.path.join(CLIPS_FOLDER, f)
        if os.stat(f_path).st_mtime < now - 1800:
            os.remove(f_path)

scheduler = BackgroundScheduler()
scheduler.add_job(func=cleanup_old_files, trigger="interval", minutes=10)
scheduler.start()

# --- المسارات (Routes) ---

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        video_url = request.form.get("youtube_url")
        uploaded_file = request.files.get("video")
        
        video_path = ""
        try:
            if video_url:
                video_path = download_youtube_video(video_url)
            elif uploaded_file:
                filename = secure_filename(uploaded_file.filename)
                video_path = os.path.join(UPLOAD_FOLDER, filename)
                uploaded_file.save(video_path)
        except Exception as e:
            return f"خطأ أثناء معالجة الفيديو: {str(e)}"
        
        if not video_path:
            return "لم يتم توفير فيديو"

        try:
            moments = get_best_moments(video_path)
            clips_urls = []
            base_name = os.path.basename(video_path).split('.')[0]
            
            for i, m in enumerate(moments):
                clip_name = f"{base_name}_clip_{i}.mp4"
                clip_local_path = os.path.join(CLIPS_FOLDER, clip_name)
                process_video_to_tiktok(video_path, clip_local_path, m['start'], m['end'])
                
                # رفع لـ Cloudinary
                try:
                    upload_result = cloudinary.uploader.upload(
                        clip_local_path, 
                        resource_type = "video",
                        folder = "tiktok_clips",
                        public_id = f"{base_name}_clip_{i}"
                    )
                    clips_urls.append(upload_result['secure_url'])
                except Exception as e:
                    print(f"Cloudinary Upload Error: {e}")
                    return f"خطأ في رفع الملف لـ Cloudinary: {str(e)}"
                
            if os.path.exists(video_path):
                os.remove(video_path)
                
            return render_template("index.html", clips=clips_urls)
        except Exception as e:
            return f"خطأ أثناء معالجة المقاطع: {str(e)}"

    return render_template("index.html", clips=[])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
