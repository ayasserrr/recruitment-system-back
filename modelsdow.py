import os

# توجيه التحميل لبارتيشن E
cache_dir = "E:/huggingface_cache"
if not os.path.exists(cache_dir):
    os.makedirs(cache_dir)

os.environ['HF_HOME'] = cache_dir
os.environ['SENTENCE_TRANSFORMERS_HOME'] = cache_dir
os.environ['HUGGINGFACE_HUB_CACHE'] = cache_dir

from sentence_transformers import SentenceTransformer
from FlagEmbedding import FlagReranker
from transformers import pipeline

print("🚀 جاري تكملة تحميل الترسانة في البارتيشن E...")

try:
    # تحميل الموديلات وتوجيهها للمسار الجديد
    SentenceTransformer('BAAI/bge-large-en-v1.5', cache_folder=cache_dir)
    FlagReranker('BAAI/bge-reranker-v2-m3', cache_dir=cache_dir)
    SentenceTransformer('sentence-transformers/all-mpnet-base-v2', cache_folder=cache_dir)
    pipeline("zero-shot-classification", model="cross-encoder/nli-deberta-v3-small", model_kwargs={"cache_dir": cache_dir})
    pipeline("zero-shot-classification", model="facebook/bart-large-mnli", model_kwargs={"cache_dir": cache_dir})
    pipeline("question-answering", model="deepset/roberta-base-squad2", model_kwargs={"cache_dir": cache_dir})
    pipeline("feature-extraction", model="microsoft/codebert-base", model_kwargs={"cache_dir": cache_dir})
    pipeline("text-classification", model="SamLowe/roberta-base-go_emotions", model_kwargs={"cache_dir": cache_dir})
    pipeline("text-classification", model="cardiffnlp/twitter-roberta-base-sentiment-latest", model_kwargs={"cache_dir": cache_dir})

    print(f"\n✅ مبروك! الترسانة جاهزة في {cache_dir}")
except Exception as e:
    print(f"❌ حدث خطأ: {e}")