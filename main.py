import os
import shutil
import mysql.connector
from mysql.connector import Error
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import requests

app = FastAPI(title="FAX Send System - Render Version")

# フォルダ設定
UPLOAD_DIR = "uploaded_files"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=UPLOAD_DIR), name="files")

# MySQL設定（環境変数またはデフォルト設定）
MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'user': os.getenv('MYSQL_USER', 'root'),
    'password': os.getenv('MYSQL_PASSWORD', ''),
    'database': os.getenv('MYSQL_DATABASE', 'fax_app_db'),
    'port': int(os.getenv('MYSQL_PORT', 3306))
}

def get_db_connection():
    try:
        connection = mysql.connector.connect(**MYSQL_CONFIG)
        return connection
    except Error as e:
        print(f"DB Connection Error: {e}")
        return None

TELNYX_API_KEY = os.getenv("TELNYX_API_KEY", "YOUR_TELNYX_API_KEY")
TELNYX_FAX_NUMBER = os.getenv("TELNYX_FAX_NUMBER", "+18005550199")

@app.get("/")
def read_root():
    return {"status": "ok", "message": "FAX Backend API is running on Render!"}

@app.post("/api/v1/send-fax-file")
async def send_fax_file(
    to_number: str = Form(...),
    file: UploadFile = File(...)
):
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    
    # 対応拡張子: PDF, JPG, JPEG, PNG
    if ext not in [".pdf", ".jpg", ".jpeg", ".png"]:
        raise HTTPException(status_code=400, detail="対応していないファイル形式です。.pdf, .jpg, .png のいずれかを指定してください。")

    save_path = os.path.join(UPLOAD_DIR, filename)
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # サーバー上のファイルURLを生成
    # Renderでは本番ホスト名が自動付与されるため相対的パスで対応
    telnyx_fax_id = None
    status = "simulated"

    if TELNYX_API_KEY != "YOUR_TELNYX_API_KEY":
        headers = {
            "Authorization": f"Bearer {TELNYX_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "connection_id": os.getenv("TELNYX_CONNECTION_ID", "YOUR_CONNECTION_ID"),
            "to": to_number,
            "from": TELNYX_FAX_NUMBER,
            "media_url": save_path
        }
        res = requests.post("https://api.telnyx.com/v2/faxes", json=data, headers=headers)
        api_response = res.json()
        telnyx_fax_id = api_response.get("data", {}).get("id")
        status = "sending" if res.status_code in [200, 201] else "failed"

    # DBに保存（DB接続可能な場合のみ）
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            insert_query = """
                INSERT INTO fax_logs (to_number, original_filename, pdf_path, status, telnyx_fax_id)
                VALUES (%s, %s, %s, %s, %s)
            """
            cursor.execute(insert_query, (to_number, filename, save_path, status, telnyx_fax_id))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"Failed to log to DB: {e}")

    return {"status": "success", "message": "送信処理完了", "filename": filename}

@app.get("/api/v1/fax-history")
def get_fax_history():
    conn = get_db_connection()
    if not conn:
        # DB未接続時のダミー応答（動作テスト用）
        return {
            "status": "success",
            "history": [
                {
                    "id": 1,
                    "to_number": "+81312345678",
                    "original_filename": "sample_test.pdf",
                    "pdf_path": "uploaded_files/sample_test.pdf",
                    "status": "sent",
                    "created_at": "2026-10-08 11:00:00"
                }
            ]
        }
    
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM fax_logs ORDER BY created_at DESC")
    logs = cursor.fetchall()
    cursor.close()
    conn.close()
    return {"status": "success", "history": logs}