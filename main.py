import os
import shutil
import mysql.connector
from mysql.connector import Error
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from docx2pdf import convert
import requests

app = FastAPI(title="FAX Send System - Enhanced UI Version")

# フォルダ設定
UPLOAD_DIR = "uploaded_files"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=UPLOAD_DIR), name="files")

# テンプレート（HTML）の設定
templates = Jinja2Templates(directory="templates")

# MySQL設定
MYSQL_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'fax_app_db',
    'port': 3306
}

def get_db_connection():
    try:
        connection = mysql.connector.connect(**MYSQL_CONFIG)
        return connection
    except Error as e:
        raise HTTPException(status_code=500, detail=f"データベース接続エラー: {str(e)}")

TELNYX_API_KEY = "YOUR_TELNYX_API_KEY"
TELNYX_FAX_NUMBER = "+18005550199"

@app.get("/")
def read_root(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.post("/api/v1/send-fax-file")
async def send_fax_file(
    to_number: str = Form(...),
    file: UploadFile = File(...)
):
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in [".pdf", ".docx"]:
        raise HTTPException(status_code=400, detail="対応していないファイル形式です。.pdf または .docx を指定してください。")

    save_path = os.path.join(UPLOAD_DIR, filename)
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    pdf_path = save_path
    if ext == ".docx":
        pdf_filename = os.path.splitext(filename)[0] + ".pdf"
        pdf_path = os.path.join(UPLOAD_DIR, pdf_filename)
        try:
            convert(save_path, pdf_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"PDFへの変換に失敗しました: {str(e)}")

    file_url = f"http://127.0.0.1:8000/files/{os.path.basename(pdf_path)}"
    telnyx_fax_id = None
    status = "simulated"

    if TELNYX_API_KEY != "YOUR_TELNYX_API_KEY":
        headers = {
            "Authorization": f"Bearer {TELNYX_API_KEY}",
            "Content-Type": "application/json"
        }
        data = {
            "connection_id": "YOUR_CONNECTION_ID",
            "to": to_number,
            "from": TELNYX_FAX_NUMBER,
            "media_url": file_url
        }
        res = requests.post("https://api.telnyx.com/v2/faxes", json=data, headers=headers)
        api_response = res.json()
        telnyx_fax_id = api_response.get("data", {}).get("id")
        status = "sending" if res.status_code in [200, 201] else "failed"

    # DBに保存
    conn = get_db_connection()
    cursor = conn.cursor()
    insert_query = """
        INSERT INTO fax_logs (to_number, original_filename, pdf_path, status, telnyx_fax_id)
        VALUES (%s, %s, %s, %s, %s)
    """
    cursor.execute(insert_query, (to_number, filename, pdf_path, status, telnyx_fax_id))
    conn.commit()
    cursor.close()
    conn.close()

    return {"status": "success", "message": "送信処理完了"}

@app.get("/api/v1/fax-history")
def get_fax_history():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM fax_logs ORDER BY created_at DESC")
    logs = cursor.fetchall()
    cursor.close()
    conn.close()
    return {"status": "success", "history": logs}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)