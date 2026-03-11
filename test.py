from sqlmodel import SQLModel, Field, create_engine, Session, select
from typing import Optional
from datetime import datetime

# 1. إعداد بيانات الاتصال مباشرة (بدل الـ .env للتجربة فقط)
# تأكدي من كتابة الباسورد بتاعك مكان 'YOUR_PASSWORD'
# واسم الداتابيز 'recruitment_system_db' اللي عملتيه في pgAdmin
DB_URL = "postgresql://postgres:12345@localhost:5432/recruitment_system_db"

# 2. تعريف موديل بسيط جداً للتجربة (نفس اللي في الـ Schema بتاعتك)
class CompanyTest(SQLModel, table=True):
    __tablename__ = "company_test"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    industry: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)

# 3. إنشاء المحرك (Engine)
engine = create_engine(DB_URL, echo=True)

def run_test():
    try:
        print("🚀 جاري محاولة الاتصال بالداتابيز وإنشاء الجدول...")
        # إنشاء الجدول في pgAdmin أوتوماتيك
        SQLModel.metadata.create_all(engine)
        print("✅ الجدول اتكريه بنجاح!")

        # محاولة إضافة بيانات
        with Session(engine) as session:
            test_comp = CompanyTest(name="Test Company", industry="AI & Tech")
            session.add(test_comp)
            session.commit()
            print("✅ تم إضافة بيانات تجريبية بنجاح!")

            # محاولة قراءة البيانات
            statement = select(CompanyTest).where(CompanyTest.name == "Test Company")
            result = session.exec(statement).first()
            if result:
                print(f"🎊 مبروك! الكونكشن سليم والداتا اتقرأت: {result.name} - {result.industry}")

    except Exception as e:
        print(f"❌ حصلت مشكلة في الاتصال: {e}")

if __name__ == "__main__":
    run_test()