# app.py

import streamlit as st
import requests
import docx
import pandas as pd
from streamlit_quill import st_quill
import io
import json
import re
import zipfile
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import urllib.parse

# Khởi tạo session state
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "user" not in st.session_state:
    st.session_state["user"] = None
if "role" not in st.session_state:
    st.session_state["role"] = None
if "google_credentials" not in st.session_state:
    st.session_state["google_credentials"] = None
if "uploaded_files" not in st.session_state:
    st.session_state["uploaded_files"] = []
if "grading_results" not in st.session_state:
    st.session_state["grading_results"] = []
if "start_exam" not in st.session_state:
    st.session_state["start_exam"] = False
if "current_num_questions" not in st.session_state:
    st.session_state["current_num_questions"] = 1
if "mssv" not in st.session_state:
    st.session_state["mssv"] = ""
if "full_name" not in st.session_state:
    st.session_state["full_name"] = ""
if "exam_access_granted" not in st.session_state:
    st.session_state["exam_access_granted"] = False

# Ẩn thanh công cụ của Streamlit
st.markdown(
    """
    <style>
    [data-testid="stToolbar"] {
        visibility: hidden;
    }
    </style>
    """,
    unsafe_allow_html=True
)

# Cấu hình API
API_URL = "https://openrouter.ai/api/v1/chat/completions"
API_KEY = st.secrets["openrouter"]["api_key"]

# Cấu hình Google OAuth
GOOGLE_CLIENT_ID = st.secrets["google_oauth"]["client_id"]
GOOGLE_CLIENT_SECRET = st.secrets["google_oauth"]["client_secret"]
GOOGLE_REDIRECT_URI = st.secrets["google_oauth"]["redirect_uri"]
SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid"
]

def set_loading_cursor(status):
    """Thay đổi con trỏ chuột khi đang tải."""
    if status:
        st.markdown(
            """
            <style>
            html, body {
                cursor: wait !important;
            }
            </style>
            """,
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            """
            <style>
            html, body {
                cursor: default !important;
            }
            </style>
            """,
            unsafe_allow_html=True
        )

def clean_markdown_headers(text):
    """Loại bỏ các tiêu đề Markdown từ văn bản."""
    lines = text.split("\n")
    cleaned_lines = [line.replace("### ", "").replace("#### ", "") for line in lines]
    return "\n".join(cleaned_lines)

def authenticate_google_drive():
    """Xác thực và kết nối với Google Drive API."""
    SCOPES = ['https://www.googleapis.com/auth/drive']
    creds_info = json.loads(st.secrets["google_drive"]["credentials"])
    creds = Credentials.from_authorized_user_info(info=creds_info, scopes=SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build('drive', 'v3', credentials=creds)

def get_or_create_folder(service, folder_name, parent_id=None):
    """Tìm hoặc tạo một thư mục trên Google Drive."""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    response = service.files().list(q=query, spaces='drive').execute()
    folders = response.get('files', [])
    if folders:
        return folders[0]['id']
    folder_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    if parent_id:
        folder_metadata['parents'] = [parent_id]
    folder = service.files().create(body=folder_metadata, fields='id').execute()
    return folder['id']

def clear_folder(service, folder_id):
    """Xóa tất cả file trong một thư mục trên Google Drive."""
    response = service.files().list(q=f"'{folder_id}' in parents and trashed=false", spaces='drive').execute()
    for file in response.get('files', []):
        service.files().delete(fileId=file['id']).execute()

def upload_file_to_drive(service, file_content, file_name, folder_id, update_if_exists=True):
    """Tải file lên Google Drive."""
    base_name = file_name.replace("_graded.docx", "")
    query = f"'{folder_id}' in parents and trashed=false"
    response = service.files().list(q=query, spaces='drive').execute()
    for file in response.get('files', []):
        if file['name'].startswith(base_name) and update_if_exists:
            service.files().delete(fileId=file['id']).execute()
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype='application/octet-stream')
    file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    permission = {
        'type': 'anyone',
        'role': 'reader'
    }
    service.permissions().create(fileId=file['id'], body=permission).execute()
    return file['id']

def download_file_from_drive(service, file_id):
    """Tải file từ Google Drive."""
    request = service.files().get_media(fileId=file_id)
    file_content = io.BytesIO()
    downloader = MediaIoBaseDownload(file_content, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    file_content.seek(0)
    return file_content.read()

def find_file_in_folder(service, file_name, folder_id):
    """Tìm file trong một thư mục trên Google Drive."""
    query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
    response = service.files().list(q=query, spaces='drive').execute()
    files = response.get('files', [])
    return files[0] if files else None

def get_exam_list(service, exams_folder_id):
    """Lấy danh sách đề thi từ Google Drive."""
    exam_secrets_file = find_file_in_folder(service, "exam_secrets.json", exams_folder_id)
    if exam_secrets_file:
        content = download_file_from_drive(service, exam_secrets_file['id'])
        return json.loads(content.decode('utf-8'))
    return []

def update_exam_list(service, exams_folder_id, exam_list):
    """Cập nhật danh sách đề thi lên Google Drive."""
    json_content = json.dumps(exam_list, ensure_ascii=False, indent=4)
    upload_file_to_drive(service, json_content.encode('utf-8'), "exam_secrets.json", exams_folder_id, update_if_exists=True)

# Khởi tạo service và root folder
service = authenticate_google_drive()
root_folder_id = get_or_create_folder(service, "ExamSystem")

def initialize_teacher_folders(service, username):
    """Khởi tạo các thư mục cho giáo viên trên Google Drive."""
    teacher_folder = get_or_create_folder(service, f"teacher_{username}", root_folder_id)
    exams_folder = get_or_create_folder(service, "exams", teacher_folder)
    essays_folder = get_or_create_folder(service, "essays", teacher_folder)
    graded_essays_folder_id = get_or_create_folder(service, "graded_essays", teacher_folder)
    reports_folder_id = get_or_create_folder(service, "reports", teacher_folder)
    return {
        "teacher_folder_id": teacher_folder,
        "exams_folder_id": exams_folder,
        "essays_folder_id": essays_folder,
        "graded_essays_folder_id": graded_essays_folder_id,
        "reports_folder_id": reports_folder_id
    }

def get_google_auth_url():
    """Tạo URL để người dùng đăng nhập bằng Google."""
    flow = InstalledAppFlow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uris": [GOOGLE_REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        scopes=SCOPES
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')
    return auth_url

def handle_google_callback(code):
    """Xử lý callback từ Google sau khi người dùng đăng nhập."""
    flow = InstalledAppFlow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uris": [GOOGLE_REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token"
            }
        },
        scopes=SCOPES
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    flow.fetch_token(code=code)
    credentials = flow.credentials
    return credentials

def get_user_info(credentials):
    """Lấy thông tin người dùng từ Google."""
    oauth2_client = build('oauth2', 'v2', credentials=credentials)
    user_info = oauth2_client.userinfo().get().execute()
    return user_info

def login_with_google():
    st.session_state["logged_in"] = False
    st.markdown("<h2 style='text-align: center; font-size: 36px;'>👤Đăng nhập hệ thống</h2>", unsafe_allow_html=True)

    auth_url = get_google_auth_url()
    st.markdown(f'<a href="{auth_url}" target="_self"><button>Đăng nhập bằng Google</button></a>', unsafe_allow_html=True)

    query_params = st.query_params
    code = query_params.get("code")
    if code:
        try:
            credentials = handle_google_callback(code)
            st.session_state["google_credentials"] = credentials
            user_info = get_user_info(credentials)
            email = user_info.get("email")
            name = user_info.get("name", email.split("@")[0])

            if email.endswith("@ntt.edu.vn"):
                role = "teacher"
            else:
                role = "student"

            st.session_state["logged_in"] = True
            st.session_state["user"] = name
            st.session_state["role"] = role
            st.session_state["email"] = email

            st.success(f"Xin chào, {name} ({role})!")
            st.rerun()
        except Exception as e:
            st.error(f"Đăng nhập thất bại: {str(e)}")
            st.write("Query parameters:", query_params)  # Debug info

def logout():
    """Xử lý đăng xuất người dùng."""
    st.session_state.clear()
    st.rerun()

def read_docx(file_content):
    """Đọc nội dung file DOCX."""
    doc = docx.Document(io.BytesIO(file_content))
    return "\n".join([para.text for para in doc.paragraphs])

def save_to_csv(data, service, folder_id):
    """Lưu dữ liệu vào file CSV trên Google Drive."""
    df = pd.DataFrame(data)
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
    existing_file = find_file_in_folder(service, "grading_report.csv", folder_id)
    if existing_file:
        existing_content = download_file_from_drive(service, existing_file['id'])
        existing_df = pd.read_csv(io.StringIO(existing_content.decode('utf-8-sig')), encoding='utf-8-sig')
        df = pd.concat([existing_df, df], ignore_index=True)
        df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        file_metadata = {'name': "grading_report.csv"}
        media = MediaIoBaseUpload(io.BytesIO(csv_buffer.getvalue().encode('utf-8')), mimetype='text/csv')
        service.files().update(fileId=existing_file['id'], body=file_metadata, media_body=media).execute()
    else:
        df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        upload_file_to_drive(service, csv_buffer.getvalue().encode('utf-8'), "grading_report.csv", folder_id)

def extract_key_points_and_keywords(answer_text):
    """
    Hàm phân tích đáp án mẫu để trích xuất các ý chính và từ khóa (keywords) nhằm chấm điểm bài tự luận.
    
    Args:
        answer_text (str): Đáp án mẫu dưới dạng văn bản.
    
    Returns:
        dict: Một dictionary chứa các ý chính, từ khóa tích cực/tiêu cực và quy tắc ngữ cảnh (nếu có).
              Trả về None nếu có lỗi xảy ra.
    """
    # Kiểm tra đầu vào
    if not answer_text or not answer_text.strip():
        print("Error: answer_text is empty or invalid")
        return None

    # Tạo prompt bằng cách chia thành các phần nhỏ
    prompt_parts = [
        "Bạn là một trợ lý AI chuyên phân tích đáp án mẫu. Dựa trên đoạn văn bản sau, ",
        "hãy phân tích thành các ý chính (key points) và trích xuất từ khóa để sử dụng trong việc chấm điểm bài tự luận.\n\n",
        
        f"**Đáp án mẫu:**\n{answer_text}\n\n",
        
        "**Yêu cầu:**\n",
        "1. Phân tích đáp án mẫu thành các ý chính (key points), mỗi ý chính có trọng số (tổng trọng số = 1).\n",
        "2. Trích xuất từ khóa tích cực (positive keywords) và từ khóa tiêu cực (negative keywords) cho từng ý chính.\n",
        "3. Thêm quy tắc ngữ cảnh (contextual rules) nếu cần (ví dụ: một số từ khóa chỉ có ý nghĩa khi xuất hiện cùng nhau).\n",
        "4. Trả về kết quả dưới dạng JSON với cấu trúc:\n",
        "   ```json\n",
        "   {\n",
        '       "Ý 1": {\n',
        '           "description": "Mô tả ý chính",\n',
        '           "weight": 0.3,\n',
        '           "positive_keywords": [\n',
        '               {"keyword": "từ khóa 1", "weight": 0.5},\n',
        '               {"keyword": "từ khóa 2", "weight": 0.3},\n',
        "               ...\n",
        "           ],\n",
        '           "negative_keywords": [\n',
        '               {"keyword": "từ khóa sai 1", "weight": -0.2},\n',
        "               ...\n",
        "           ],\n",
        '           "contextual_rule": "Quy tắc ngữ cảnh (nếu có)"\n',
        "       },\n",
        '       "Ý 2": { ... }\n',
        "   }\n",
        "   ```\n\n",
        
        "Bắt đầu phân tích:"
    ]
    
    # Nối các phần của prompt lại
    prompt = "".join(prompt_parts)

    # Cấu hình headers cho API request
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Cấu hình payload (dữ liệu gửi đi) cho API
    data = {
        "model": "mistralai/mistral-small-3.1-24b-instruct:free",
        "messages": [
            {"role": "system", "content": "Bạn là một trợ lý AI chuyên phân tích đáp án mẫu."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3
    }
    
    # Gửi yêu cầu đến API và xử lý kết quả
    try:
        response = requests.post(API_URL, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            result = response.json()["choices"][0]["message"]["content"]
            return json.loads(result)
        else:
            print(f"API request failed with status code: {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error during API request: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON response: {e}")
        return None

def grade_essay(student_text, answer_text, student_name=None, mssv=None, key_points=None):
    """
    Hàm chấm điểm bài tự luận của sinh viên dựa trên đáp án mẫu.
    
    Args:
        student_text (str): Bài làm của sinh viên.
        answer_text (str): Đáp án mẫu.
        student_name (str, optional): Tên sinh viên.
        mssv (str, optional): Mã số sinh viên.
        key_points (dict, optional): Danh sách ý chính và từ khóa.
    
    Returns:
        str: Kết quả chấm điểm dưới dạng văn bản. Trả về None nếu có lỗi.
    """
    # Nếu key_points chưa có, phân tích đáp án mẫu để lấy key_points
    if key_points is None:
        set_loading_cursor(True)
        with st.spinner("Đang phân tích đáp án mẫu..."):
            key_points = extract_key_points_and_keywords(answer_text)
        set_loading_cursor(False)
        if not key_points:
            return None

    # Tạo prompt bằng cách chia thành các phần nhỏ
    prompt_parts = [
        "Bạn là một giảng viên chấm bài chuyên nghiệp. Hãy chấm bài tự luận sau đây bằng cách so sánh bài làm của sinh viên với đáp án mẫu.\n\n",
        
        f"**Đáp án mẫu:**\n{answer_text}\n\n",
        
        f"**Bài làm của sinh viên:**\n{student_text}\n\n",
        
        "**Danh sách ý chính và từ khóa:**\n",
        "Dưới đây là danh sách các ý chính (key points) và từ khóa (keywords) để bạn đánh giá bài làm. ",
        "Sử dụng các ý chính và từ khóa này để xác định mức độ phù hợp của bài làm với đáp án mẫu.\n\n"
    ]
    
    # Thêm thông tin về các ý chính và từ khóa vào prompt
    for point, data in key_points.items():
        prompt_parts.append(f"**{point} (trọng số: {data['weight']}):** {data['description']}\n")
        prompt_parts.append("Từ khóa tích cực (positive keywords):\n")
        for kw in data["positive_keywords"]:
            prompt_parts.append(f"- '{kw['keyword']}' (trọng số: {kw['weight']})\n")
        prompt_parts.append("Từ khóa tiêu cực (negative keywords):\n")
        for kw in data.get("negative_keywords", []):
            prompt_parts.append(f"- '{kw['keyword']}' (trọng số: {kw['weight']})\n")
        if "contextual_rule" in data:
            prompt_parts.append(f"Quy tắc ngữ cảnh: {data['contextual_rule']}\n")
    
    # Thêm yêu cầu chấm bài và ví dụ định dạng kết quả
    prompt_parts.extend([
        "\n**Yêu cầu chấm bài:**\n",
        "1. Đưa ra nhận xét chi tiết về bài làm của sinh viên:\n",
        "   - Kiểm tra xem bài làm có chứa các ý chính không (dựa trên từ khóa tích cực).\n",
        "   - Trừ điểm nếu bài làm chứa từ khóa tiêu cực.\n",
        "   - Đánh giá mức độ chi tiết, tính chính xác, và ví dụ minh họa (nếu có).\n",
        "2. Chấm điểm trên thang 10, tính điểm dựa trên trọng số của ý chính và từ khóa:\n",
        "   - Nếu ý chính được đề cập đầy đủ (có từ khóa tích cực), cộng điểm theo trọng số.\n",
        "   - Nếu ý chính thiếu hoặc có từ khóa tiêu cực, trừ điểm.\n",
        "3. Ghi rõ tổng điểm của bài làm theo định dạng: **Tổng điểm: [số điểm]** (dòng cuối cùng).\n\n",
        
        "**Ví dụ định dạng kết quả:**\n",
        "Nhận xét chi tiết về bài làm của sinh viên:\n\n",
        "**Ý 1:**\n",
        "- Sinh viên giải thích đúng khái niệm.\n",
        "- Điểm trừ: Thiếu ví dụ bổ sung.\n\n",
        "**Ý 2:**\n",
        "- Sinh viên mô tả đúng một phần.\n",
        "- Điểm trừ: Thiếu giải thích chi tiết.\n\n",
        "Điểm:\n",
        "- Ý 1: **7.0**\n",
        "- Ý 2: **5.5**\n\n",
        "**Tổng điểm: 6.0**\n\n",
        
        "Bắt đầu chấm bài:"
    ])
    
    # Nối các phần của prompt lại
    prompt = "".join(prompt_parts)

    # Cấu hình headers cho API request
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Cấu hình payload (dữ liệu gửi đi) cho API
    data = {
        "model": "mistralai/mistral-small-3.1-24b-instruct:free",
        "messages": [
            {"role": "system", "content": "Bạn là một giảng viên chấm bài chuyên nghiệp."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3
    }
    
    # Gửi yêu cầu đến API và xử lý kết quả
    try:
        response = requests.post(API_URL, headers=headers, json=data, timeout=30)
        if response.status_code == 200:
            grading_result = response.json()["choices"][0]["message"]["content"]
            if student_name and mssv:
                total_score = extract_score(grading_result)
                data = {
                    "MSSV": [mssv],
                    "Họ và Tên": [student_name],
                    "Điểm Tổng": [total_score],
                    "Kết quả chấm điểm": [grading_result]
                }
                save_to_csv(data, service, reports_folder_id)
            return grading_result
        else:
            print(f"API request failed with status code: {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error during API request: {e}")
        return None

def extract_score(grading_result):
    """Trích xuất điểm số từ kết quả chấm điểm."""
    matches = re.findall(r"Tổng điểm:\s*(\d+(\.\d+)?)", grading_result, re.IGNORECASE)
    if matches:
        return float(matches[-1][0])
    matches = re.findall(r"Điểm:\s*(\d+(\.\d+)?)", grading_result, re.IGNORECASE)
    if matches:
        return float(matches[-1][0])
    matches = re.findall(r"Điểm:\s*(\d+(\.\d+)?)/\d+", grading_result, re.IGNORECASE)
    if matches:
        return float(matches[-1][0])
    matches = re.findall(r"Score:\s*(\d+(\.\d+)?)", grading_result, re.IGNORECASE)
    if matches:
        return float(matches[-1][0])
    matches = re.findall(r"(\d+(\.\d+)?)/10", grading_result)
    if matches:
        return float(matches[-1][0])
    matches = re.findall(r"^\s*(\d+(\.\d+)?)\s*$", grading_result, re.MULTILINE)
    if matches:
        return float(matches[-1][0])
    return 0.0

def load_grading_report(service, folder_id):
    """Tải báo cáo chấm điểm từ Google Drive."""
    file = find_file_in_folder(service, "grading_report.csv", folder_id)
    if file:
        content = download_file_from_drive(service, file['id'])
        return pd.read_csv(io.StringIO(content.decode('utf-8-sig')), encoding='utf-8-sig')
    return None

# Logic chính của ứng dụng
if not st.session_state["logged_in"]:
    login_with_google()
else:
    role = st.session_state.get("role", "student")
    if role == "student":
        st.markdown("<h1 style='text-align: center; font-size: 40px;'>Hệ thống thi tự luận trực tuyến NTTU</h1>", unsafe_allow_html=True)
    else:
        st.markdown("<h1 style='text-align: center; font-size: 40px;'>🎓Hệ thống chấm tự luận bằng AI</h1>", unsafe_allow_html=True)
    
    st.write(f"Xin chào, {st.session_state['user']} ({st.session_state['role']})!")
    if st.button("Đăng xuất"):
        logout()
    
    if role == "teacher":
        teacher_folders = initialize_teacher_folders(service, st.session_state["email"])
        exams_folder_id = teacher_folders["exams_folder_id"]
        essays_folder_id = teacher_folders["essays_folder_id"]
        graded_essays_folder_id = teacher_folders["graded_essays_folder_id"]
        reports_folder_id = teacher_folders["reports_folder_id"]
        
        st.subheader("Tải đề thi và đáp án")
        exam_list = get_exam_list(service, exams_folder_id)
        if exam_list:
            st.info("Danh sách đề thi hiện có:")
            for exam in exam_list:
                subject_code = exam.get("subject_code", "N/A")
                term = exam.get("term", "N/A")
                subject_name = exam.get("subject_name", "N/A")
                st.write(f"- {subject_code} - {term} - {subject_name} - {exam['exam_file']} (Mã số bí mật: {exam['secret_code']})")
        
        col1, col2 = st.columns(2)
        with col1:
            if exam_list and st.button("Xóa tất cả đề thi"):
                for exam in exam_list:
                    service.files().delete(fileId=exam['exam_id']).execute()
                    service.files().delete(fileId=exam['answer_id']).execute()
                exam_secrets_file = find_file_in_folder(service, "exam_secrets.json", exams_folder_id)
                if exam_secrets_file:
                    service.files().delete(fileId=exam_secrets_file['id']).execute()
                st.success("Đã xóa tất cả đề thi và đáp án.")
                st.rerun()
        
        st.subheader("Tải lên đề thi mới")
        uploaded_exam_pdf = st.file_uploader("Tải lên đề thi (PDF)", type=["pdf"], key="exam_pdf")
        uploaded_answer = st.file_uploader("Tải lên đáp án mẫu", type=["docx"], key="answer")
        subject_code = st.text_input("Mã học phần (ví dụ: IT001):", key="subject_code")
        term = st.text_input("Tên lớn (ví dụ: Kỳ 1 - 2024):", key="term")
        subject_name = st.text_input("Tên môn học (ví dụ: Lập trình Python):", key="subject_name")
        secret_code = st.text_input("Nhập mã số bí mật cho đề thi:", type="password", key="secret_code")
        
        if st.button("Tải lên đề thi"):
            if not uploaded_exam_pdf or not uploaded_answer:
                st.error("Vui lòng tải lên cả file đề thi (PDF) và đáp án mẫu (DOCX).")
            elif not subject_code or not term or not subject_name or not secret_code:
                st.error("Vui lòng nhập đầy đủ Mã học phần, Tên lớn, Tên môn học và Mã số bí mật.")
            else:
                exam_pdf_content = uploaded_exam_pdf.read()
                answer_content = uploaded_answer.read()
                exam_count = len(exam_list) + 1
                exam_filename = f"de_thi_{exam_count}.pdf"
                answer_filename = f"dap_an_{exam_count}.docx"
                exam_file_id = upload_file_to_drive(service, exam_pdf_content, exam_filename, exams_folder_id, update_if_exists=True)
                answer_file_id = upload_file_to_drive(service, answer_content, answer_filename, exams_folder_id, update_if_exists=True)
                exam_list.append({
                    "exam_file": exam_filename,
                    "exam_id": exam_file_id,
                    "answer_file": answer_filename,
                    "answer_id": answer_file_id,
                    "secret_code": secret_code,
                    "subject_code": subject_code,
                    "term": term,
                    "subject_name": subject_name
                })
                update_exam_list(service, exams_folder_id, exam_list)
                st.success(f"Đề thi {exam_filename} và đáp án đã được lưu trên Google Drive.")
                st.rerun()

        tab1, tab2, tab3 = st.tabs(["Chấm bài đơn", "Chấm bài hàng loạt", "Xem báo cáo"])

        with tab1:
            uploaded_essay = st.file_uploader("Tải lên bài làm tự luận của sinh viên", type=["docx"], key="single_essay")
            if uploaded_essay:
                exam_list = get_exam_list(service, exams_folder_id)
                if exam_list:
                    display_names = [f"{exam['subject_code']} - {exam['term']} - {exam['subject_name']}" for exam in exam_list]
                    selected_display_name = st.selectbox("Chọn đáp án mẫu:", display_names, key="select_exam_single")
                    selected_exam = next(exam for exam in exam_list 
                                       if f"{exam['subject_code']} - {exam['term']} - {exam['subject_name']}" == selected_display_name)
                    answer_content = download_file_from_drive(service, selected_exam['answer_id'])
                    answer_text = read_docx(answer_content)
                    filename = uploaded_essay.name
                    try:
                        mssv, student_name = filename.replace(".docx", "").split("_", 1)
                    except ValueError:
                        st.error("Tên file không đúng định dạng 'MSSV_HọTên.docx'. Vui lòng kiểm tra lại.")
                    else:
                        student_text = read_docx(uploaded_essay.read())
                        set_loading_cursor(True)
                        with st.spinner("Đang chấm bài..."):
                            result = grade_essay(student_text, answer_text, student_name, mssv)
                        set_loading_cursor(False)
                        if result:
                            st.subheader("Kết quả chấm điểm:")
                            st.write(f"MSSV: {mssv}")
                            st.write(f"Họ và Tên: {student_name}")
                            st.write(result)
                            cleaned_result = clean_markdown_headers(result)
                            graded_filename = f"{mssv}_{student_name}_graded.docx"
                            doc = docx.Document()
                            doc.add_paragraph(f"MSSV: {mssv}")
                            doc.add_paragraph(f"Họ và Tên: {student_name}")
                            doc.add_paragraph(cleaned_result)
                            doc_buffer = io.BytesIO()
                            doc.save(doc_buffer)
                            doc_buffer.seek(0)
                            upload_file_to_drive(service, doc_buffer.getvalue(), graded_filename, graded_essays_folder_id)
                            st.success(f"Kết quả đã được lưu trên Google Drive với tên: {graded_filename}")
                            st.download_button(
                                label="Tải kết quả chấm điểm",
                                data=doc_buffer.getvalue(),
                                file_name=graded_filename,
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                            )
                else:
                    st.error("Không tìm thấy đáp án mẫu trên Google Drive. Vui lòng tải lên đáp án trước.")

        with tab2:
            if "start_grading" not in st.session_state:
                st.session_state["start_grading"] = False
            uploaded_essays = st.file_uploader("Tải lên nhiều bài làm tự luận", type=["docx"], accept_multiple_files=True, key="batch_essays")
            MAX_FILES = 10
            if uploaded_essays and len(uploaded_essays) > MAX_FILES:
                st.error(f"Vui lòng chỉ tải lên tối đa {MAX_FILES} file để chấm hàng loạt.")
                uploaded_essays = uploaded_essays[:MAX_FILES]
            current_files = [file.name for file in uploaded_essays] if uploaded_essays else []
            if current_files != st.session_state["uploaded_files"]:
                st.session_state["uploaded_files"] = current_files
                st.session_state["grading_results"] = []
                st.session_state["start_grading"] = False
            if uploaded_essays:
                exam_list = get_exam_list(service, exams_folder_id)
                if exam_list:
                    display_names = [f"{exam['subject_code']} - {exam['term']} - {exam['subject_name']}" for exam in exam_list]
                    selected_display_name = st.selectbox("Chọn đáp án mẫu:", display_names, key="select_exam_batch")
                    if st.button("Chấm bài"):
                        st.session_state["start_grading"] = True
                        st.session_state["grading_results"] = []
                        set_loading_cursor(True)
                        with st.spinner("Đang xóa các file kết quả cũ..."):
                            clear_folder(service, graded_essays_folder_id)
                        set_loading_cursor(False)
                        selected_exam = next(exam for exam in exam_list 
                                           if f"{exam['subject_code']} - {exam['term']} - {exam['subject_name']}" == selected_display_name)
                        answer_content = download_file_from_drive(service, selected_exam['answer_id'])
                        answer_text = read_docx(answer_content)
                        results = []
                        set_loading_cursor(True)
                        with st.spinner("Đang chấm bài hàng loạt..."):
                            for idx, essay_file in enumerate(uploaded_essays, 1):
                                filename = essay_file.name
                                try:
                                    mssv, student_name = filename.replace(".docx", "").split("_", 1)
                                except ValueError:
                                    st.warning(f"Tên file {filename} không đúng định dạng 'MSSV_HọTên.docx'. Bỏ qua.")
                                    continue
                                student_text = read_docx(essay_file.read())
                                grading_result = grade_essay(student_text, answer_text, student_name, mssv)
                                if grading_result:
                                    total_score = extract_score(grading_result)
                                    results.append({
                                        "STT": idx,
                                        "MSSV": mssv,
                                        "Họ và Tên": student_name,
                                        "Tổng điểm tự luận": total_score
                                    })
                                    cleaned_result = clean_markdown_headers(grading_result)
                                    graded_filename = f"{mssv}_{student_name}_graded.docx"
                                    doc = docx.Document()
                                    doc.add_paragraph(f"MSSV: {mssv}")
                                    doc.add_paragraph(f"Họ và Tên: {student_name}")
                                    doc.add_paragraph(cleaned_result)
                                    doc_buffer = io.BytesIO()
                                    doc.save(doc_buffer)
                                    doc_buffer.seek(0)
                                    upload_file_to_drive(service, doc_buffer.getvalue(), graded_filename, graded_essays_folder_id)
                        set_loading_cursor(False)
                        st.session_state["grading_results"] = results
                else:
                    st.error("Không tìm thấy đáp án mẫu trên Google Drive. Vui lòng tải lên đáp án trước.")
            if st.session_state["grading_results"]:
                df = pd.DataFrame(st.session_state["grading_results"])
                st.subheader("Kết quả chấm điểm hàng loạt:")
                st.dataframe(df)
                csv_buffer = io.StringIO()
                df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
                csv = csv_buffer.getvalue().encode('utf-8')
                st.download_button(
                    label="Tải báo cáo CSV",
                    data=csv,
                    file_name="batch_grading_report.csv",
                    mime="text/csv"
                )
                st.success("Đã chấm xong tất cả bài và lưu kết quả trên Google Drive.")
                st.subheader("Tải kết quả chi tiết cho sinh viên:")
                response = service.files().list(q=f"'{graded_essays_folder_id}' in parents and trashed=false", spaces='drive').execute()
                file_list = response.get('files', [])
                if file_list:
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                        for file in file_list:
                            if file['name'].endswith("_graded.docx") and "_graded_graded" not in file['name']:
                                set_loading_cursor(True)
                                with st.spinner(f"Đang xử lý file {file['name']}..."):
                                    file_content = download_file_from_drive(service, file['id'])
                                set_loading_cursor(False)
                                if file_content:
                                    zip_file.writestr(file['name'], file_content)
                    zip_buffer.seek(0)
                    st.download_button(
                        label="Tải tất cả kết quả (ZIP)",
                        data=zip_buffer,
                        file_name="all_graded_essays.zip",
                        mime="application/zip",
                        key="download_all_graded"
                    )
                else:
                    st.info("Chưa có kết quả chấm điểm nào được lưu.")
            elif uploaded_essays and not st.session_state["start_grading"]:
                st.info("Vui lòng chọn đáp án mẫu và nhấn 'Chấm bài' để bắt đầu chấm điểm.")
            elif uploaded_essays:
                st.info("Đang xử lý bài làm...")

        with tab3:
            df = load_grading_report(service, reports_folder_id)
            if df is not None:
                st.subheader("Báo cáo điểm tổng hợp:")
                st.dataframe(df)
                csv_buffer = io.StringIO()
                df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
                csv = csv_buffer.getvalue().encode('utf-8')
                st.download_button(
                    label="Tải báo cáo tổng hợp CSV",
                    data=csv,
                    file_name="grading_report_total.csv",
                    mime="text/csv"
                )
            else:
                st.info("Chưa có báo cáo nào được lưu.")
    
    elif role == "student":
        response = service.files().list(q=f"'{root_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false", spaces='drive').execute()
        teacher_folders = response.get('files', [])
        all_exams = []
        for teacher_folder in teacher_folders:
            username = teacher_folder['name'].replace("teacher_", "")
            exams_folder = find_file_in_folder(service, "exams", teacher_folder['id'])
            if exams_folder:
                exam_list = get_exam_list(service, exams_folder['id'])
                for exam in exam_list:
                    subject_code = exam.get("subject_code", "N/A")
                    term = exam.get("term", "N/A")
                    subject_name = exam.get("subject_name", "N/A")
                    display_name = f"{subject_code} - {term} - {subject_name} - {username}"
                    all_exams.append({
                        "display_name": display_name,
                        "exam_id": exam['exam_id'],
                        "secret_code": exam['secret_code']
                    })
        if all_exams:
            mssv = st.text_input("MSSV:", value=st.session_state["mssv"], key="mssv_input")
            full_name = st.text_input("Họ và Tên:", value=st.session_state["full_name"], key="full_name_input")
            st.session_state["mssv"] = mssv
            st.session_state["full_name"] = full_name
            if st.session_state["mssv"] and st.session_state["full_name"]:
                selected_exam = st.selectbox("Chọn đề thi:", [exam["display_name"] for exam in all_exams])
                secret_code = st.text_input("Nhập mã số bí mật:", type="password")
                if st.button("Xem đề thi"):
                    selected_exam_data = next(exam for exam in all_exams if exam["display_name"] == selected_exam)
                    if secret_code == selected_exam_data["secret_code"]:
                        st.session_state["selected_exam_id"] = selected_exam_data["exam_id"]
                        st.session_state["exam_access_granted"] = True
                        st.rerun()
                    else:
                        st.error("Mã số bí mật không đúng. Vui lòng thử lại.")
                if st.session_state.get("exam_access_granted", False):
                    tab1, tab2 = st.tabs(["Làm bài thi online", "Nộp bài"])
                    with tab1:
                        if not st.session_state["start_exam"]:
                            if st.button("Làm bài"):
                                st.session_state["start_exam"] = True
                                st.session_state["current_num_questions"] = 1
                                st.rerun()
                        else:
                            st.subheader("Đề thi:")
                            file_id = st.session_state["selected_exam_id"]
                            viewer_url = f"https://drive.google.com/viewerng/viewer?embedded=true&url=https://drive.google.com/uc?id={file_id}"
                            pdf_display = f'<iframe src="{viewer_url}" width="100%" height="600px" frameborder="0"></iframe>'
                            st.markdown(pdf_display, unsafe_allow_html=True)
                            st.info("Nếu đề thi không hiển thị, vui lòng sử dụng nút 'Tải đề thi (PDF) nếu không xem được' để tải file về và xem.")
                            set_loading_cursor(True)
                            with st.spinner("Đang tải đề thi..."):
                                exam_content = download_file_from_drive(service, file_id)
                            set_loading_cursor(False)
                            st.download_button(
                                label="Tải đề thi (PDF) nếu không xem được",
                                data=exam_content,
                                file_name="de_thi.pdf",
                                mime="application/pdf"
                            )
                            answers = []
                            for i in range(st.session_state["current_num_questions"]):
                                st.write(f"**Câu {i+1}**")
                                answer = st_quill(f"Câu {i+1}:", key=f"answer_{i}")
                                answers.append(answer)
                            col1, col2 = st.columns(2)
                            with col1:
                                if st.button("Thêm câu hỏi"):
                                    st.session_state["current_num_questions"] += 1
                                    st.rerun()
                            with col2:
                                if st.session_state["current_num_questions"] > 1:
                                    if st.button("Loại câu hỏi"):
                                        st.session_state["current_num_questions"] -= 1
                                        st.rerun()
                            if st.button("Nộp bài"):
                                student_text = "\n".join([f"Câu {i+1}:\n{answer}" for i, answer in enumerate(answers) if answer])
                                filename = f"{st.session_state['mssv']}_{st.session_state['full_name']}.docx"
                                doc = docx.Document()
                                doc.add_paragraph(student_text)
                                doc_buffer = io.BytesIO()
                                doc.save(doc_buffer)
                                doc_buffer.seek(0)
                                teacher_username = selected_exam.split(" - ")[-1]
                                teacher_folder = get_or_create_folder(service, f"teacher_{teacher_username}", root_folder_id)
                                essays_folder = get_or_create_folder(service, "essays", teacher_folder)
                                upload_file_to_drive(service, doc_buffer.getvalue(), filename, essays_folder)
                                st.success(f"Bài làm đã được lưu trên Google Drive với tên: {filename}")
                                st.session_state["start_exam"] = False
                                st.session_state["current_num_questions"] = 1
                                st.session_state["exam_access_granted"] = False
                                st.rerun()
                    with tab2:
                        uploaded_essay = st.file_uploader("Tải lên bài làm tự luận", type=["docx"])
                        if uploaded_essay:
                            filename = f"{st.session_state['mssv']}_{st.session_state['full_name']}.docx"
                            essay_content = uploaded_essay.read()
                            teacher_username = selected_exam.split(" - ")[-1]
                            teacher_folder = get_or_create_folder(service, f"teacher_{teacher_username}", root_folder_id)
                            essays_folder = get_or_create_folder(service, "essays", teacher_folder)
                            upload_file_to_drive(service, essay_content, filename, essays_folder)
                            st.success(f"Bài làm đã được lưu trên Google Drive với tên: {filename}")
        else:
            st.error("Không tìm thấy đề thi nào. Vui lòng liên hệ giáo viên.")
