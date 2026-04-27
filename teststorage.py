import shutil

total, used, free = shutil.disk_usage("/")
print(f"المساحة الكلية: {total // (2**30)} GB")
print(f"المساحة المستخدمة: {used // (2**30)} GB")
print(f"المساحة الفاضية: {free // (2**30)} GB")

if (free // (2**30)) < 15:
    print("⚠️ تحذير: المساحة قليلة جداً، فضي مساحة قبل ما تبدأي!")
else:
    print("✅ المساحة تمام، توكلي على الله.")