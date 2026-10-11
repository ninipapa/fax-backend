import os
import uuid
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.staticfiles import StaticFiles
import requests

app = FastAPI(title="FAX Send System - Real Transmission Version")

UPLOAD_DIR = "uploaded_files"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=UPLOAD_DIR), name="files")

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if not DATABASE_URL:
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"DB Connection Error: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fax_logs (
                    id SERIAL PRIMARY KEY,
                    to_number VARCHAR(50),
                    original_filename VARCHAR(255),
                    pdf_path VARCHAR(255),
                    status VARCHAR(50),
                    telnyx_fax_id VARCHAR(255),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"Failed to init DB: {e}")

init_db()

TELNYX_API_KEY = os.getenv("TELNYX_API_KEY")
TELNYX_FAX_NUMBER = os.getenv("TELNYX_FAX_NUMBER")
TELNYX_CONNECTION_ID = os.getenv("TELNYX_CONNECTION_ID")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "FAX Backend is ready for live transmission!"}

@app.post("/api/v1/send-fax-file")
async def send_fax_file(
    request: Request,
    to_number: str = Form(...),
    file: UploadFile = File(...)
):
    original_filename = file.filename
    ext = os.path.splitext(original_filename)[1].lower()
    
    if ext not in [".pdf", ".jpg", ".jpeg", ".png"]:
        raise HTTPException(status_code=400, detail="対応していないファイル形式です。.pdf, .jpg, .png のいずれかを指定してください。")

    # URL安全な英数字のユニークファイル名でサーバーに保存
    safe_filename = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(UPLOAD_DIR, safe_filename)
    
    with open(save_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)

    # Telnyx 参照用の公開 URL (例: https://fax-backend-app.onrender.com/files/a1b2c3d4.pdf)
    base_url = str(request.base_url).rstrip("/")
    media_url = f"{base_url}/files/{safe_filename}"

    telnyx_fax_id = None
    status = "simulated"

    if TELNYX_API_KEY and TELNYX_FAX_NUMBER and TELNYX_CONNECTION_ID:
        headers = {
            "Authorization": f"Bearer {TELNYX_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "connection_id": TELNYX_CONNECTION_ID,
            "to": to_number,
            "from": TELNYX_FAX_NUMBER,
            "media_url": media_url,
            "webhook_url": f"{base_url}/api/v1/telnyx-webhook"
        }
        res = requests.post("https://api.telnyx.com/v2/faxes", json=data, headers=headers)
        api_response = res.json()
        print(f"Telnyx Response: {api_response}")
        
        if res.status_code in [200, 201]:
            telnyx_fax_id = api_response.get("data", {}).get("id")
            status = "sending"
        else:
            status = "failed"

    # PostgreSQL には「元のファイル名（original_filename）」を記録
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            insert_query = """
                INSERT INTO fax_logs (to_number, original_filename, pdf_path, status, telnyx_fax_id)
                VALUES (%s, %s, %s, %s, %s)
            """
            cursor.execute(insert_query, (to_number, original_filename, save_path, status, telnyx_fax_id))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"Failed to log to DB: {e}")

    return {"status": "success", "message": "送信処理完了", "filename": original_filename, "fax_status": status}

import sys # 冒頭に sys が無ければインポートを確認してください

# Telnyx からのステータス更新通知（Webhook）を受領するエンドポイント
@app.post("/api/v1/telnyx-webhook")
async def telnyx_webhook(request: Request):
    try:
        body = await request.json()
        data = body.get("data", {})
        event_type = data.get("event_type", "unknown")
        payload = data.get("payload", {})
        fax_id = payload.get("id")
        failure_reason = payload.get("failure_reason")

        # どんなイベントであってもログに強制出力（flush=True）
        print(f"\n----------------------------------------", flush=True)
        print(f"★ Telnyx Event Received: {event_type}", flush=True)
        print(f"★ Fax ID: {fax_id}", flush=True)
        if failure_reason:
            print(f"⚠️ FAILURE REASON: {failure_reason}", flush=True)
        print(f"----------------------------------------\n", flush=True)

        # DB ステータス更新
        if fax_id:
            new_status = None
            if event_type == "fax.delivered":
                new_status = "delivered"
            elif event_type in ["fax.failed", "fax.media.processed"]:
                if failure_reason or event_type == "fax.failed":
                    new_status = "failed"
            elif event_type in ["fax.queued", "fax.sending"]:
                new_status = "sending"

            if new_status:
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("UPDATE fax_logs SET status = %s WHERE telnyx_fax_id = %s", (new_status, fax_id))
                    conn.commit()
                    cursor.close()
                    conn.close()
                    print(f"DB Status updated to: {new_status}", flush=True)
    except Exception as e:
        print(f"Webhook error: {e}", flush=True)
    return {"status": "ok"}

@app.get("/api/v1/fax-history")
def get_fax_history():
    conn = get_db_connection()
    if not conn:
        return {"status": "error", "message": "DB未接続", "history": []}
    
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT id, to_number, original_filename, pdf_path, status, telnyx_fax_id, created_at::text FROM fax_logs ORDER BY created_at DESC")
        logs = cursor.fetchall()
        cursor.close()
        conn.close()
        return {"status": "success", "history": logs}
    except Exception as e:
        return {"status": "error", "message": str(e), "history": []}