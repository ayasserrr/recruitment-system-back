import sys
import subprocess
import platform
import shutil

def check_requirements():
    print("🔍 جاري فحص جاهزية النظام للـ AI Pipeline...\n")

    # 1. فحص نسخة بايثون
    python_version = sys.version_info
    if python_version.major == 3 and python_version.minor >= 8:
        print(f"✅ نسخة بايثون تمام: {sys.version.split()[0]}")
    else:
        print(f"❌ نسخة بايثون قديمة: {sys.version.split()[0]} (يفضل 3.9 أو أحدث)")

    # 2. فحص وجود C++ Build Tools (مهم لـ SHAP و XGBoost)
    try:
        # بنحاول نشوف هل فيه مبرمج (Compiler) موجود
        result = subprocess.run(['where', 'cl'], capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ Microsoft C++ Build Tools: موجود")
        else:
            # اختبار بديل لو المبرمج مش في الـ Path بس موجود
            print("⚠️ تنبيه: C++ Compiler مش باين في الـ PATH. لو مكتبة SHAP فشلت في التثبيت، لازم تسطبي Visual Studio Build Tools.")
    except Exception:
        print("⚠️ مش قادر أتأكد من وجود C++ Compiler.")

    # 3. فحص كارت الشاشة (NVIDIA GPU)
    try:
        import torch
        if torch.cuda.is_available():
            print(f"🚀 كارت شاشة NVIDIA جاهز: {torch.cuda.get_device_name(0)}")
            print(f"🔹 نسخة CUDA: {torch.version.cuda}")
        else:
            print("ℹ️ مفيش كارت شاشة NVIDIA مدعوم. الموديلات هتشتغل على الـ CPU (هتكون أبطأ شوية بس هتشتغل).")
    except ImportError:
        print("ℹ️ مكتبة Torch مش متسطبة لسه، هعرف حالة الكارت وقت التثبيت.")

    # 4. فحص الـ RAM (الذاكرة العشوائية)
    try:
        import psutil
        ram = psutil.virtual_memory()
        total_ram = ram.total / (1024**3)
        if total_ram >= 15:
            print(f"✅ الرامات ممتازة: {total_ram:.1f} GB")
        elif total_ram >= 7:
            print(f"⚠️ الرامات متوسطة: {total_ram:.1f} GB (اقفلي الكروم وأي برامج تانية وانتي شغالة)")
        else:
            print(f"❌ الرامات ضعيفة: {total_ram:.1f} GB (قد تواجهي بطء شديد)")
    except ImportError:
        print("ℹ️ مكتبة psutil مش موجودة، مش قادر أحسب الرامات بدقة.")

    # 5. فحص الاتصال بـ HuggingFace (عشان تحميل الموديلات)
    print("\n🌐 بختبر الاتصال بمخزن الموديلات (HuggingFace)...")
    try:
        import http.client
        conn = http.client.HTTPSConnection("huggingface.co", timeout=5)
        conn.request("HEAD", "/")
        res = conn.getresponse()
        if res.status == 200:
            print("✅ الاتصال بـ HuggingFace تمام.")
    except Exception:
        print("❌ فيه مشكلة في الاتصال بـ HuggingFace. تأكدي من النت.")

check_requirements()