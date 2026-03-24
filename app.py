import os
import subprocess
import boto3
import time
import json
import re
from flask import Flask, render_template, request, jsonify
from botocore.client import Config
from werkzeug.utils import secure_filename
import yt_dlp
import google.generativeai as genai
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta

app = Flask(__name__)

# --- الإعدادات ---
UPLOAD_FOLDER = 'static/uploads'
CLIPS_FOLDER = os.path.join(UPLOAD_FOLDER, "clips")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(CLIPS_FOLDER, exist_ok=True)

# إعدادات ClawCloud S3
S3_ACCESS_KEY = '7cpqtw4y'
S3_SECRET_KEY = 'ffrn6gnxbsq4bchb'
S3_ENDPOINT = 'https://objectstorageapi.us-east-1.clawcloudrun.com'
S3_BUCKET_NAME = 'video-clips'

# إعدادات Gemini AI
GEMINI_API_KEY = 'AIzaSyA2KpuB4kAF9SiX9qdLeVmKVKKKdGngirk'
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# تهيئة عميل S3
s3_client = boto3.client(
    's3',
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    endpoint_url=S3_ENDPOINT,
    config=Config(signature_version='s3v4', s3={'addressing_style': 'path'})
)

def ensure_bucket_exists():
    try:
        s3_client.head_bucket(Bucket=S3_BUCKET_NAME)
    except:
        s3_client.create_bucket(Bucket=S3_BUCKET_NAME)

# --- وظائف المساعدة ---

def download_youtube_video(url):
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': os.path.join(UPLOAD_FOLDER, '%(id)s.%(ext)s'),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)

def get_best_moments(video_path):
    # في نسخة حقيقية، يمكننا إرسال لقطات أو صوت لـ Gemini
    # هنا سنطلب منه اقتراح أوقات عشوائية ذكية بناءً على طول الفيديو (كمثال)
    # ملاحظة: Gemini 1.5 Flash يدعم تحليل الفيديو مباشرة إذا تم رفعه
    prompt = "أنا أقوم بتقسيم فيديو إلى مقاطع TikTok. اقترح لي 3 لحظات مثيرة (بداية ونهاية بالثواني) لفيديو مدته غير معروفة، افترض أنه طويل. أجب بتنسيق JSON فقط: [{'start': 10, 'end': 40}, ...]"
    try:
        response = model.generate_content(prompt)
        # استخراج JSON من النص
        match = re.search(r'\[.*\]', response.text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except:
        pass
    return [{'start': 30, 'end': 60}, {'start': 120, 'end': 150}] # قيم افتراضية في حال الفشل

def process_video_to_tiktok(input_path, output_path, start_time, end_time):
    duration = end_time - start_time
    # FFmpeg: قص الوقت + تحويل الأبعاد لـ 9:16 (TikTok) مع Crop من المنتصف
    # التنسيق: 1080x1920
    cmd = (
        f'ffmpeg -ss {start_time} -t {duration} -i "{input_path}" '
        f'-vf "crop=ih*(9/16):ih,scale=1080:1920" '
        f'-c:v libx264 -crf 23 -preset veryfast -c:a copy "{output_path}" -y'
    )
    subprocess.call(cmd, shell=True)

def cleanup_old_files():
    """حذف الملفات من S3 والمجلد المحلي بعد 30 دقيقة"""
    print("Running cleanup task...")
    # حذف محلي
    now = time.time()
    for f in os.listdir(CLIPS_FOLDER):
        f_path = os.path.join(CLIPS_FOLDER, f)
        if os.stat(f_path).st_mtime < now - 1800: # 30 mins
            os.remove(f_path)
    # ملاحظة: حذف S3 يحتاج تتبع للأوقات في قاعدة بيانات، هنا سنكتفي بالمحلي للتبسيط

# إعداد المجدول للحذف التلقائي
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
        if video_url:
            video_path = download_youtube_video(video_url)
        elif uploaded_file:
            filename = secure_filename(uploaded_file.filename)
            video_path = os.path.join(UPLOAD_FOLDER, filename)
            uploaded_file.save(video_path)
        
        if not video_path:
            return "لم يتم توفير فيديو"

        # 1. الحصول على أفضل اللحظات بالذكاء الاصطناعي
        moments = get_best_moments(video_path)
        
        # 2. معالجة المقاطع (TikTok Format) ورفعها لـ S3
        ensure_bucket_exists()
        clips_urls = []
        base_name = os.path.basename(video_path).split('.')[0]
        
        for i, m in enumerate(moments):
            clip_name = f"{base_name}_clip_{i}.mp4"
            clip_local_path = os.path.join(CLIPS_FOLDER, clip_name)
            
            # تحويل لـ TikTok
            process_video_to_tiktok(video_path, clip_local_path, m['start'], m['end'])
            
            # رفع لـ S3
            s3_key = f"tiktok_clips/{base_name}/{clip_name}"
            s3_client.upload_file(clip_local_path, S3_BUCKET_NAME, s3_key)
            
            url = f"{S3_ENDPOINT}/{S3_BUCKET_NAME}/{s3_key}"
            clips_urls.append(url)
            
        # 3. حذف الفيديو الأصلي فوراً
        if os.path.exists(video_path):
            os.remove(video_path)
            
        return render_template("index.html", clips=clips_urls)

    return render_template("index.html", clips=[])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
