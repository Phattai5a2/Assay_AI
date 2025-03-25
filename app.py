#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Mar 24 07:48:40 2025

@author: phattai
"""

import streamlit as st
import requests
import docx
import pandas as pd
from streamlit_quill import st_quill
import base64
import os
import io
import webbrowser
import json
import re
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

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

# API URL và API Key cho OpenRouter
API_URL = "https://openrouter.ai/api/v1/chat/completions"
try:
    API_KEY = st.secrets["openrouter"]["api_key"]
except KeyError:
    st.error("Không tìm thấy API key của OpenRouter trong Secrets.")
    st.stop()

# Cấu hình Google OAuth từ st.secrets
GOOGLE_CLIENT_ID = st.secrets["google_oauth"]["client_id"]
GOOGLE_CLIENT_SECRET = st.secrets["google_oauth"]["client_secret"]
GOOGLE_REDIRECT_URI = st.secrets["google_oauth"]["redirect_uri"]

# Scopes cần thiết
SCOPES = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
    "https://www.googleapis.com/auth/drive"
]

# Hàm tạo URL đăng nhập Google
def get_google_login_url():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uris": [GOOGLE_REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true"
    )
    st.session_state["oauth_state"] = state
    return authorization_url

# Hàm lấy thông tin người dùng từ Google
def get_user_info(credentials):
    user_info_service = build("oauth2", "v2", credentials=credentials)
    user_info = user_info_service.userinfo().get().execute()
    return user_info

# Hàm xử lý callback sau khi đăng nhập Google
def handle_google_callback():
    if "code" not in st.query_params or "oauth_state" not in st.session_state:
        return None

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uris": [GOOGLE_REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        state=st.session_state["oauth_state"]
    )
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    flow.fetch_token(code=st.query_params["code"])
    credentials = flow.credentials
    user_info = get_user_info(credentials)

    email = user_info.get("email", "").lower()
    if email.endswith("@ntt.edu.vn"):
        role = "teacher"
    else:
        role = "student"
    st.session_state["role"] = role

    return user_info, credentials

# Hàm đăng nhập
def login():
    if "user_info" not in st.session_state:
        st.session_state["user_info"] = None
    if "credentials" not in st.session_state:
        st.session_state["credentials"] = None

    if "code" in st.query_params:
        user_info, credentials = handle_google_callback()
        if user_info:
            st.session_state["user_info"] = user_info
            st.session_state["credentials"] = credentials.to_json()
            st.session_state["logged_in"] = True
            st.query_params.clear()
            st.rerun()

    if not st.session_state.get("logged_in"):
        st.markdown(
            "<h2 style='text-align: center; font-size: 36px; color: #333;'>Đăng nhập hệ thống</h2>",
            unsafe_allow_html=True
        )
        login_url = get_google_login_url()
        st.markdown(
            f"<a href='{login_url}' style='display: inline-block; padding: 10px 20px; color: white; background-color: #4285F4; text-decoration: none; border-radius: 5px;'>Đăng nhập bằng Google</a>",
            unsafe_allow_html=True
        )

# Hàm đăng xuất
def logout():
    st.session_state.clear()
    st.rerun()

# Hàm tạo hoặc lấy ID của thư mục trên Google Drive
def get_or_create_folder(service, folder_name, parent_id=None):
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    response = service.files().list(q=query, spaces='drive').execute()
    folders = response.get('files', [])
    
    if folders:
        return folders[0]['id']
    else:
        folder_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            folder_metadata['parents'] = [parent_id]
        folder = service.files().create(body=folder_metadata, fields='id').execute()
        return folder['id']

# Hàm tải file lên Google Drive
def upload_file_to_drive(service, file_content, file_name, folder_id):
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype='application/octet-stream')
    file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    return file['id']

# Hàm tải file từ Google Drive
def download_file_from_drive(service, file_id):
    request = service.files().get_media(fileId=file_id)
    file_content = io.BytesIO()
    downloader = MediaIoBaseDownload(file_content, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    file_content.seek(0)
    return file_content.read()

# Hàm tìm file trên Google Drive
def find_file_in_folder(service, file_name, folder_id):
    query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
    response = service.files().list(q=query, spaces='drive').execute()
    files = response.get('files', [])
    return files[0] if files else None

# Hàm lưu thông tin đề thi vào exam_metadata.csv
def save_exam_metadata(service, exam_data, folder_id):
    df = pd.DataFrame([exam_data])
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
    
    existing_file = find_file_in_folder(service, "exam_metadata.csv", folder_id)
    if existing_file:
        existing_content = download_file_from_drive(service, existing_file['id']).decode('utf-8-sig')
        existing_df = pd.read_csv(io.StringIO(existing_content), encoding='utf-8-sig')
        df = pd.concat([existing_df, df], ignore_index=True)
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        file_metadata = {'name': "exam_metadata.csv"}
        media = MediaIoBaseUpload(io.BytesIO(csv_buffer.getvalue().encode('utf-8')), mimetype='text/csv')
        service.files().update(fileId=existing_file['id'], body=file_metadata, media_body=media).execute()
    else:
        upload_file_to_drive(service, csv_buffer.getvalue().encode('utf-8'), "exam_metadata.csv", folder_id)

# Hàm đọc thông tin đề thi từ exam_metadata.csv
def load_exam_metadata(service, folder_id):
    file = find_file_in_folder(service, "exam_metadata.csv", folder_id)
    if file:
        content = download_file_from_drive(service, file['id']).decode('utf-8-sig')
        return pd.read_csv(io.StringIO(content), encoding='utf-8-sig')
    return pd.DataFrame(columns=["Exam ID", "Class Code", "Exam Name", "File ID", "Created Date", "Subject"])

# Hàm lưu thông tin bài nộp vào submissions.csv
def save_submission_metadata(service, submission_data, folder_id):
    df = pd.DataFrame([submission_data])
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
    
    existing_file = find_file_in_folder(service, "submissions.csv", folder_id)
    if existing_file:
        existing_content = download_file_from_drive(service, existing_file['id']).decode('utf-8-sig')
        existing_df = pd.read_csv(io.StringIO(existing_content), encoding='utf-8-sig')
        df = pd.concat([existing_df, df], ignore_index=True)
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        file_metadata = {'name': "submissions.csv"}
        media = MediaIoBaseUpload(io.BytesIO(csv_buffer.getvalue().encode('utf-8')), mimetype='text/csv')
        service.files().update(fileId=existing_file['id'], body=file_metadata, media_body=media).execute()
    else:
        upload_file_to_drive(service, csv_buffer.getvalue().encode('utf-8'), "submissions.csv", folder_id)

# Hàm đọc thông tin bài nộp từ submissions.csv
def load_submission_metadata(service, folder_id):
    file = find_file_in_folder(service, "submissions.csv", folder_id)
    if file:
        content = download_file_from_drive(service, file['id']).decode('utf-8-sig')
        return pd.read_csv(io.StringIO(content), encoding='utf-8-sig')
    return pd.DataFrame(columns=["Submission ID", "Student Email", "Class Code", "File ID", "Submission Time", "Assigned Teacher"])

# Hàm đọc thông tin phân công lớp học từ class_assignments.csv
def load_class_assignments(service, folder_id):
    file = find_file_in_folder(service, "class_assignments.csv", folder_id)
    if file:
        content = download_file_from_drive(service, file['id']).decode('utf-8-sig')
        return pd.read_csv(io.StringIO(content), encoding='utf-8-sig')
    return pd.DataFrame(columns=["Class Code", "Teacher Email"])

# Hàm đọc file Word
def read_docx(file_content):
    doc = docx.Document(io.BytesIO(file_content))
    text = "\n".join([para.text for para in doc.paragraphs])
    return text

# Hàm chấm điểm bài tự luận
def grade_essay(student_text, answer_text, student_name=None, mssv=None):
    prompt = f"""Bạn là giáo viên. Hãy chấm bài sau đây.
    \n\nĐáp án mẫu:\n{answer_text}
    \n\nBài làm của học sinh:\n{student_text}
    \n\nHãy đưa ra số điểm (thang 10) và nhận xét chi tiết. Định dạng điểm phải là: Điểm: [số điểm] (ví dụ: Điểm: 8.5)"""
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": "mistralai/mistral-small-3.1-24b-instruct:free",
        "messages": [{"role": "system", "content": "Bạn là một giáo viên chấm bài chuyên nghiệp."},
                     {"role": "user", "content": prompt}],
        "temperature": 0.7
    }
    
    try:
        response = requests.post(API_URL, headers=headers, json=payload)
        if response.status_code == 200:
            grading_result = response.json()["choices"][0]["message"]["content"]
            return grading_result
        else:
            st.error(f"Lỗi API: {response.status_code} - {response.json()}")
            return None
    except requests.exceptions.RequestException as e:
        st.error(f"Lỗi kết nối mạng: {str(e)}")
        return None

# Hàm trích xuất điểm từ kết quả chấm
def extract_score(grading_result):
    match = re.search(r"Điểm:\s*(\d+(\.\d+)?)", grading_result, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return 0.0

# Giao diện chính
if "logged_in" not in st.session_state or not st.session_state["logged_in"]:
    login()
else:
    user_info = st.session_state["user_info"]
    user_email = user_info.get("email")
    credentials_json = st.session_state["credentials"]
    credentials = Credentials.from_authorized_user_info(json.loads(credentials_json), SCOPES)
    service = build('drive', 'v3', credentials=credentials)

    # Tạo các thư mục trên Google Drive
    root_folder_id = get_or_create_folder(service, "ExamSystem")
    exams_folder_id = get_or_create_folder(service, "exams", root_folder_id)
    essays_folder_id = get_or_create_folder(service, "essays", root_folder_id)
    graded_essays_folder_id = get_or_create_folder(service, "graded_essays", root_folder_id)

    st.markdown(
        "<h1 style='text-align: center; font-size: 40px;'>Hệ thống chấm bài thi tự luận bằng AI</h1>",
        unsafe_allow_html=True
    )
    st.write(f"Xin chào, {user_info.get('name', 'Người dùng')} ({user_email})!")
    if st.button("Đăng xuất"):
        logout()

    role = st.session_state.get("role", "student")
    
    if role == "teacher":
        tab1, tab2, tab3, tab4, tab5 = st.tabs(["Chấm bài đơn", "Chấm bài hàng loạt", "Dữ liệu cá nhân", "Bài nộp của sinh viên", "Quản lý đề thi"])
        
        with tab1:
            uploaded_essay = st.file_uploader("Tải lên bài làm tự luận của sinh viên", type=["docx"], key="single_essay")
            
            if uploaded_essay:
                answer_file = find_file_in_folder(service, "dap_an.docx", exams_folder_id)
                if answer_file:
                    filename = uploaded_essay.name
                    try:
                        mssv, student_name = filename.replace(".docx", "").split("_", 1)
                    except ValueError:
                        st.error("Tên file không đúng định dạng 'MSSV_HọTên.docx'.")
                    else:
                        student_text = read_docx(uploaded_essay.read())
                        answer_content = download_file_from_drive(service, answer_file['id'])
                        answer_text = read_docx(answer_content)
                        result = grade_essay(student_text, answer_text, student_name, mssv)
                        if result:
                            st.subheader("Kết quả chấm điểm:")
                            st.write(f"MSSV: {mssv}")
                            st.write(f"Họ và Tên: {student_name}")
                            st.write(result)
                            
                            graded_filename = f"{mssv}_{student_name}_graded.docx"
                            doc = docx.Document()
                            doc.add_paragraph(f"MSSV: {mssv}")
                            doc.add_paragraph(f"Họ và Tên: {student_name}")
                            doc.add_paragraph(result)
                            doc_buffer = io.BytesIO()
                            doc.save(doc_buffer)
                            doc_buffer.seek(0)
                            
                            user_folder_id = get_or_create_folder(service, user_email, graded_essays_folder_id)
                            upload_file_to_drive(service, doc_buffer.getvalue(), graded_filename, user_folder_id)
                            st.success(f"Kết quả đã được lưu trên Google Drive với tên: {graded_filename}")
                else:
                    st.error("Không tìm thấy đáp án mẫu trên Google Drive.")

        with tab2:
            uploaded_essays = st.file_uploader("Tải lên nhiều bài làm tự luận", type=["docx"], accept_multiple_files=True, key="batch_essays")
            if uploaded_essays:
                answer_file = find_file_in_folder(service, "dap_an.docx", exams_folder_id)
                if answer_file:
                    answer_content = download_file_from_drive(service, answer_file['id'])
                    answer_text = read_docx(answer_content)
                    results = []
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
                            
                            graded_filename = f"{mssv}_{student_name}_graded.docx"
                            doc = docx.Document()
                            doc.add_paragraph(f"MSSV: {mssv}")
                            doc.add_paragraph(f"Họ và Tên: {student_name}")
                            doc.add_paragraph(grading_result)
                            doc_buffer = io.BytesIO()
                            doc.save(doc_buffer)
                            doc_buffer.seek(0)
                            
                            user_folder_id = get_or_create_folder(service, user_email, graded_essays_folder_id)
                            upload_file_to_drive(service, doc_buffer.getvalue(), graded_filename, user_folder_id)
                    if results:
                        df = pd.DataFrame(results)
                        st.subheader("Kết quả chấm điểm hàng loạt:")
                        st.dataframe(df)
                        st.success("Đã chấm xong tất cả bài và lưu kết quả trên Google Drive.")
                else:
                    st.error("Không tìm thấy đáp án mẫu trên Google Drive.")

        with tab3:
            st.subheader("Dữ liệu cá nhân của bạn")
            user_folder = find_file_in_folder(service, user_email, graded_essays_folder_id)
            if user_folder:
                query = f"'{user_folder['id']}' in parents and trashed=false"
                response = service.files().list(q=query, spaces='drive').execute()
                user_files = response.get('files', [])
                if user_files:
                    st.write("Danh sách kết quả chấm điểm của bạn:")
                    for file in user_files:
                        st.write(f"- {file['name']}")
                        file_content = download_file_from_drive(service, file['id'])
                        st.download_button(
                            label=f"Tải file: {file['name']}",
                            data=file_content,
                            file_name=file['name'],
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        )
                else:
                    st.info("Chưa có dữ liệu cá nhân nào.")
            else:
                st.info("Chưa có dữ liệu cá nhân nào.")

        with tab4:
            st.subheader("Bài nộp của sinh viên")
            class_assignments = load_class_assignments(service, root_folder_id)
            teacher_classes = class_assignments[class_assignments["Teacher Email"] == user_email]["Class Code"].tolist()
            
            if not teacher_classes:
                st.info("Bạn chưa được phân công lớp nào.")
            else:
                submissions = load_submission_metadata(service, root_folder_id)
                teacher_submissions = submissions[submissions["Class Code"].isin(teacher_classes)]
                
                if not teacher_submissions.empty:
                    st.write("Danh sách bài nộp từ các lớp bạn phụ trách:")
                    for _, submission in teacher_submissions.iterrows():
                        st.write(f"- Sinh viên: {submission['Student Email']}, Lớp: {submission['Class Code']}, Thời gian nộp: {submission['Submission Time']}")
                        file_content = download_file_from_drive(service, submission["File ID"])
                        st.download_button(
                            label=f"Tải bài nộp: {submission['Student Email']}",
                            data=file_content,
                            file_name=f"{submission['Student Email']}_exam.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        )
                else:
                    st.info("Chưa có bài nộp nào từ các lớp bạn phụ trách.")

        with tab5:
            st.subheader("Quản lý đề thi")
            class_code = st.text_input("Nhập mã lớp (ví dụ: CS101):", key="exam_class_code")
            exam_name = st.text_input("Tên đề thi (ví dụ: Đề thi giữa kỳ 2025):")
            subject = st.text_input("Môn học (ví dụ: Lập trình Python):")
            uploaded_exam = st.file_uploader("Tải lên đề thi (PDF)", type=["pdf"], key="exam_pdf")
            
            if uploaded_exam and class_code and exam_name and subject:
                created_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                filename = f"{exam_name.replace(' ', '_')}_{created_date.replace(' ', '_')}.pdf"
                
                # Lưu đề thi vào thư mục theo mã lớp
                class_folder_id = get_or_create_folder(service, class_code, exams_folder_id)
                file_id = upload_file_to_drive(service, uploaded_exam.read(), filename, class_folder_id)
                
                # Lưu thông tin đề thi vào exam_metadata.csv
                exam_data = {
                    "Exam ID": str(uuid.uuid4()),
                    "Class Code": class_code,
                    "Exam Name": exam_name,
                    "File ID": file_id,
                    "Created Date": created_date,
                    "Subject": subject
                }
                save_exam_metadata(service, exam_data, root_folder_id)
                
                st.success(f"Đề thi đã được lưu trên Google Drive với tên: {filename}")

    elif role == "student":
        tab1, tab2, tab3 = st.tabs(["Làm bài thi online", "Nộp bài", "Dữ liệu cá nhân"])
        
        with tab1:
            if "start_exam" not in st.session_state:
                st.session_state["start_exam"] = False

            if not st.session_state["start_exam"]:
                class_code = st.text_input("Nhập mã lớp (ví dụ: CS101):", key="student_class_code")
                if class_code:
                    exam_metadata = load_exam_metadata(service, root_folder_id)
                    class_exams = exam_metadata[exam_metadata["Class Code"] == class_code]
                    
                    if not class_exams.empty:
                        st.write("Danh sách đề thi của lớp:")
                        selected_exam = st.selectbox(
                            "Chọn đề thi:",
                            options=class_exams["Exam Name"].tolist(),
                            format_func=lambda x: f"{x} ({class_exams[class_exams['Exam Name'] == x]['Subject'].iloc[0]})"
                        )
                        if st.button("Làm bài"):
                            st.session_state["start_exam"] = True
                            st.session_state["current_num_questions"] = 1
                            st.session_state["selected_exam"] = class_exams[class_exams["Exam Name"] == selected_exam].iloc[0]
                            st.session_state["class_code"] = class_code
                            st.rerun()
                    else:
                        st.error("Không tìm thấy đề thi nào cho lớp này.")
            else:
                selected_exam = st.session_state["selected_exam"]
                class_code = st.session_state["class_code"]
                
                st.subheader(f"Đề thi: {selected_exam['Exam Name']} ({selected_exam['Subject']})")
                file_id = selected_exam["File ID"]
                viewer_url = f"https://drive.google.com/viewerng/viewer?embedded=true&url=https://drive.google.com/uc?id={file_id}"
                pdf_display = f'<iframe src="{viewer_url}" width="100%" height="600px" frameborder="0"></iframe>'
                st.markdown(pdf_display, unsafe_allow_html=True)

                if "current_num_questions" not in st.session_state:
                    st.session_state["current_num_questions"] = 1

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
                    submission_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    filename = f"{user_email}_{submission_time.replace(' ', '_')}_exam.docx"
                    
                    # Lưu bài nộp vào thư mục theo mã lớp
                    class_folder_id = get_or_create_folder(service, class_code, essays_folder_id)
                    student_folder_id = get_or_create_folder(service, user_email, class_folder_id)
                    file_id = upload_file_to_drive(service, student_text.encode('utf-8'), filename, student_folder_id)
                    
                    # Tìm giảng viên phụ trách lớp
                    class_assignments = load_class_assignments(service, root_folder_id)
                    assigned_teacher = class_assignments[class_assignments["Class Code"] == class_code]["Teacher Email"].iloc[0] if class_code in class_assignments["Class Code"].values else "Chưa gán"
                    
                    # Lưu thông tin bài nộp vào submissions.csv
                    submission_data = {
                        "Submission ID": str(uuid.uuid4()),
                        "Student Email": user_email,
                        "Class Code": class_code,
                        "File ID": file_id,
                        "Submission Time": submission_time,
                        "Assigned Teacher": assigned_teacher
                    }
                    save_submission_metadata(service, submission_data, root_folder_id)
                    
                    st.success(f"Bài làm đã được lưu trên Google Drive với tên: {filename}")
                    st.session_state["start_exam"] = False
                    st.session_state["current_num_questions"] = 1
                    st.session_state.pop("selected_exam", None)
                    st.session_state.pop("class_code", None)
                    st.rerun()
        
        with tab2:
            class_code = st.text_input("Nhập mã lớp (ví dụ: CS101):", key="upload_class_code")
            uploaded_essay = st.file_uploader("Tải lên bài làm tự luận", type=["docx"])
            if uploaded_essay and class_code:
                submission_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                filename = f"{user_email}_{submission_time.replace(' ', '_')}_uploaded.docx"
                
                # Lưu bài nộp vào thư mục theo mã lớp
                class_folder_id = get_or_create_folder(service, class_code, essays_folder_id)
                student_folder_id = get_or_create_folder(service, user_email, class_folder_id)
                file_id = upload_file_to_drive(service, uploaded_essay.read(), filename, student_folder_id)
                
                # Tìm giảng viên phụ trách lớp
                class_assignments = load_class_assignments(service, root_folder_id)
                assigned_teacher = class_assignments[class_assignments["Class Code"] == class_code]["Teacher Email"].iloc[0] if class_code in class_assignments["Class Code"].values else "Chưa gán"
                
                # Lưu thông tin bài nộp vào submissions.csv
                submission_data = {
                    "Submission ID": str(uuid.uuid4()),
                    "Student Email": user_email,
                    "Class Code": class_code,
                    "File ID": file_id,
                    "Submission Time": submission_time,
                    "Assigned Teacher": assigned_teacher
                }
                save_submission_metadata(service, submission_data, root_folder_id)
                
                st.success(f"Bài làm đã được lưu trên Google Drive với tên: {filename}")
        
        with tab3:
            st.subheader("Dữ liệu cá nhân của bạn")
            user_files = []
            class_folders = service.files().list(q=f"'{essays_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false", spaces='drive').execute().get('files', [])
            for class_folder in class_folders:
                student_folder = find_file_in_folder(service, user_email, class_folder['id'])
                if student_folder:
                    query = f"'{student_folder['id']}' in parents and trashed=false"
                    response = service.files().list(q=query, spaces='drive').execute()
                    user_files.extend(response.get('files', []))
            
            if user_files:
                st.write("Danh sách bài làm của bạn:")
                for file in user_files:
                    st.write(f"- {file['name']}")
                    file_content = download_file_from_drive(service, file['id'])
                    st.download_button(
                        label=f"Tải file: {file['name']}",
                        data=file_content,
                        file_name=file['name'],
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    )
            else:
                st.info("Chưa có bài làm nào.")
