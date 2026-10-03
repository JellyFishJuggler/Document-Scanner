# Document Scanner API
#
# Python 3.12 chuna hai (3.14 nahi) kyunki container ke liye yahi sabse safe hai:
# numpy ka cp312 wheel confirmed hai aur opencv ka wheel cp37-abi3 hai to 3.7 se
# 3.13 tak kahin bhi chalega. Local venv 3.14 par hai - versions same hain, sirf
# interpreter alag hai.

FROM python:3.12-slim

# ---- deps pehle: alag layer taaki code change par pip cache reuse ho ----
COPY requirements.txt .

# opencv-python-headless chuna hai (opencv-python nahi) kyunki server par koi
# GUI nahi chahiye - isse libGL ka ~200MB ka gfwah nahi lagta.
RUN pip install --no-cache-dir --no-compile -r requirements.txt

# ---- app code ----
WORKDIR /app
COPY scanner.py api.py ./

# ---- non-root user: security ke liye ----
RUN useradd --create-home --uid 10001 appuser
USER appuser

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/health', timeout=4).status==200 else 1)"

# api.py ka __main__ block PORT env var padhta hai - Render PORT randomly assign
# karta hai, isliye hardcode karna galat hota. workers=1 taaki in-memory rate
# limit ek hi process mein rahe.
CMD ["python", "api.py"]