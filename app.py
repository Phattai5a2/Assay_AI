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

# Sử dụng OpenRouter API miễn phí
API_URL = "https://openrouter.ai/api/v1/chat/completions"
API_KEY = "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"  # Thay bằng API key miễn phí từ OpenRouter

# Danh sách user giả lập
USERS = {
    "teacher": "1",
    "student": "1"
}

# Xác thực Google Drive
def authenticate_google_drive():
    SCOPES = ['https://www.googleapis.com/auth/drive']
    
    # Kiểm tra sự tồn tại của file client_secrets.json
    if not os.path.exists("client_secrets.json"):
        error_msg = (
            "Không tìm thấy file client_secrets.json trong thư mục hiện tại.\n"
            "Vui lòng làm theo các bước sau:\n"
            "1. Truy cập Google Cloud Console (https://console.cloud.google.com/).\n"
            "2. Tạo một dự án và bật Google Drive API.\n"
            "3. Tạo OAuth 2.0 Client ID (chọn 'Desktop app').\n"
            "4. Tải file client_secrets.json và đặt vào thư mục chứa file app.py.\n"
            "Sau đó, chạy lại code."
        )
        print(error_msg)
        st.error(error_msg)
        raise FileNotFoundError("Thiếu file client_secrets.json")

    creds = None
    # Tải thông tin xác thực từ file credentials.json nếu tồn tại
    if os.path.exists("credentials.json"):
        try:
            with open("credentials.json", "r") as f:
                creds_info = json.load(f)
            # Kiểm tra các trường bắt buộc
            required_fields = ['client_id', 'client_secret', 'refresh_token', 'token_uri']
            if not all(field in creds_info for field in required_fields):
                print("File credentials.json thiếu các trường bắt buộc. Tạo lại credentials...")
                creds = None
            else:
                creds = Credentials.from_authorized_user_info(info=creds_info, scopes=SCOPES)
                # Làm mới token nếu đã hết hạn
                if creds and creds.expired and creds.refresh_token:
                    try:
                        creds.refresh(Request())
                        # Lưu lại credentials đã làm mới
                        with open("credentials.json", "w") as f:
                            f.write(creds.to_json())
                    except Exception as e:
                        print(f"Lỗi khi làm mới token: {str(e)}")
                        creds = None
        except json.JSONDecodeError as e:
            print(f"Lỗi khi đọc file credentials.json (định dạng JSON không hợp lệ): {str(e)}")
            creds = None
        except Exception as e:
            print(f"Lỗi khi tải credentials.json: {str(e)}")
            creds = None
    
    # Nếu không có credentials hoặc credentials không hợp lệ, thực hiện xác thực mới
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            "client_secrets.json", SCOPES
        )
        try:
            # Thử mở trình duyệt tự động
            creds = flow.run_local_server(port=0)
        except Exception as e:
            print(f"Không thể mở trình duyệt tự động: {str(e)}")
            print("Vui lòng thực hiện xác thực thủ công.")
            
            # Tạo URL xác thực
            flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
            auth_url, _ = flow.authorization_url(prompt='consent')
            
            # Mở URL trong trình duyệt thủ công
            print(f"Vui lòng mở URL sau trong trình duyệt của bạn:\n{auth_url}")
            webbrowser.open(auth_url)
            
            # Yêu cầu người dùng nhập mã xác thực
            auth_code = input("Nhập mã xác thực từ trình duyệt: ")
            
            # Lấy token từ mã xác thực
            flow.fetch_token(code=auth_code)
            creds = flow.credentials
        
        # Lưu credentials vào file
        with open("credentials.json", "w") as f:
            f.write(creds.to_json())
    
    # Tạo service để tương tác với Google Drive
    service = build('drive', 'v3', credentials=creds)
    return service

# Tạo hoặc lấy ID của thư mục trên Google Drive
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

# Tải file lên Google Drive
def upload_file_to_drive(service, file_content, file_name, folder_id):
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype='application/octet-stream')
    file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    return file['id']

# Tải file từ Google Drive
def download_file_from_drive(service, file_id):
    request = service.files().get_media(fileId=file_id)
    file_content = io.BytesIO()
    downloader = MediaIoBaseDownload(file_content, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    file_content.seek(0)
    return file_content.read()

# Tìm file trên Google Drive
def find_file_in_folder(service, file_name, folder_id):
    query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
    response = service.files().list(q=query, spaces='drive').execute()
    files = response.get('files', [])
    return files[0] if files else None

# Khởi tạo Google Drive
try:
    service = authenticate_google_drive()
except FileNotFoundError:
    st.stop()  # Dừng ứng dụng nếu không tìm thấy file client_secrets.json

# Tạo các thư mục trên Google Drive
root_folder_id = get_or_create_folder(service, "ExamSystem")
exams_folder_id = get_or_create_folder(service, "exams", root_folder_id)
essays_folder_id = get_or_create_folder(service, "essays", root_folder_id)
graded_essays_folder_id = get_or_create_folder(service, "graded_essays", root_folder_id)
reports_folder_id = get_or_create_folder(service, "reports", root_folder_id)

# Hàm kiểm tra đăng nhập
def login():
    st.session_state["logged_in"] = False
    
    st.markdown(
        """
        <h2 style='text-align: center; font-size: 36px; color: #fefcfa;'>🎓 Đăng nhập Hệ thống</h2>
        """,
        unsafe_allow_html=True
    )
    
    user = st.text_input("Tên đăng nhập:")
    password = st.text_input("Mật khẩu:", type="password")
    if st.button("Đăng nhập"):
        if user in USERS and USERS[user] == password:
            st.session_state["logged_in"] = True
            st.session_state["user"] = user
            st.session_state["role"] = "teacher" if user == "teacher" else "student"
            st.success(f"Xin chào, {user}!")
        else:
            st.error("Sai tài khoản hoặc mật khẩu!")

# Hàm đăng xuất
def logout():
    st.session_state.clear()
    st.rerun()

# Hàm đọc file Word
def read_docx(file_content):
    doc = docx.Document(io.BytesIO(file_content))
    text = "\n".join([para.text for para in doc.paragraphs])
    return text

# Hàm lưu vào CSV trên Google Drive với mã hóa UTF-8-SIG
def save_to_csv(data, service, folder_id):
    df = pd.DataFrame(data)
    csv_buffer = io.StringIO()
    # Lưu DataFrame vào buffer với mã hóa utf-8-sig
    df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
    
    existing_file = find_file_in_folder(service, "grading_report.csv", folder_id)
    if existing_file:
        # Đọc file hiện có với mã hóa utf-8-sig
        existing_content = download_file_from_drive(service, existing_file['id']).decode('utf-8-sig')
        existing_df = pd.read_csv(io.StringIO(existing_content), encoding='utf-8-sig')
        df = pd.concat([existing_df, df], ignore_index=True)
        # Lưu lại vào buffer với mã hóa utf-8-sig
        df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        file_metadata = {'name': "grading_report.csv"}
        media = MediaIoBaseUpload(io.BytesIO(csv_buffer.getvalue().encode('utf-8')), mimetype='text/csv')
        service.files().update(fileId=existing_file['id'], body=file_metadata, media_body=media).execute()
    else:
        # Lưu file mới với mã hóa utf-8-sig
        df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        upload_file_to_drive(service, csv_buffer.getvalue().encode('utf-8'), "grading_report.csv", folder_id)

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
    match = re.search(r"Score:\s*(\d+(\.\d+)?)", grading_result, re.IGNORECASE)
    if match:
        return float(match.group(1))
    match = re.search(r"(\d+(\.\d+)?)/10", grading_result)
    if match:
        return float(match.group(1))
    match = re.search(r"^\s*(\d+(\.\d+)?)\s*$", grading_result, re.MULTILINE)
    if match:
        return float(match.group(1))
    st.warning(f"Không thể trích xuất điểm từ kết quả: {grading_result}")
    return 0.0

# Hàm đọc báo cáo từ Google Drive với mã hóa UTF-8-SIG
def load_grading_report(service, folder_id):
    file = find_file_in_folder(service, "grading_report.csv", folder_id)
    if file:
        content = download_file_from_drive(service, file['id']).decode('utf-8-sig')
        return pd.read_csv(io.StringIO(content), encoding='utf-8-sig')
    return None

# Hàm mã hóa file PDF thành base64 để nhúng vào HTML
def get_base64_of_file(file_content):
    return base64.b64encode(file_content).decode()

# Giao diện chính


if "logged_in" not in st.session_state or not st.session_state["logged_in"]:
    login()
else:
    st.markdown(
        "<h1 style='text-align: center; font-size: 40px;'>Hệ thống chấm bài thi tự luận bằng AI</h1>",
        unsafe_allow_html=True
    )
    st.write(f"Xin chào, {st.session_state['user']}!")
    if st.button("Đăng xuất"):
        logout()
    
    role = st.session_state.get("role", "student")
    
    if role == "teacher":
        st.subheader("Tải đề thi và đáp án")
        uploaded_exam_pdf = st.file_uploader("Tải lên đề thi (PDF)", type=["pdf"], key="exam_pdf")
        uploaded_answer = st.file_uploader("Tải lên đáp án mẫu", type=["docx"], key="answer")
        num_questions = st.number_input("Số lượng câu hỏi trong đề thi (không bắt buộc):", min_value=1, max_value=100, value=1, step=1)

        if uploaded_exam_pdf and uploaded_answer:
            # Tải file lên Google Drive
            exam_pdf_content = uploaded_exam_pdf.read()
            answer_content = uploaded_answer.read()
            
            upload_file_to_drive(service, exam_pdf_content, "de_thi.pdf", exams_folder_id)
            upload_file_to_drive(service, answer_content, "dap_an.docx", exams_folder_id)
            
            st.session_state["num_questions"] = num_questions
            st.success("Đề thi (PDF) và đáp án đã được lưu trên Google Drive.")

        tab1, tab2, tab3 = st.tabs(["Chấm bài đơn", "Chấm bài hàng loạt", "Xem báo cáo"])

        with tab1:
            uploaded_essay = st.file_uploader("Tải lên bài làm tự luận của sinh viên", type=["docx"], key="single_essay")
            
            if uploaded_essay:
                answer_file = find_file_in_folder(service, "dap_an.docx", exams_folder_id)
                if answer_file:
                    filename = uploaded_essay.name
                    try:
                        mssv, student_name = filename.replace(".docx", "").split("_", 1)
                    except ValueError:
                        st.error("Tên file không đúng định dạng 'MSSV_HọTên.docx'. Vui lòng kiểm tra lại.")
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
            if "uploaded_files" not in st.session_state:
                st.session_state["uploaded_files"] = []
            if "grading_results" not in st.session_state:
                st.session_state["grading_results"] = []

            uploaded_essays = st.file_uploader("Tải lên nhiều bài làm tự luận", type=["docx"], accept_multiple_files=True, key="batch_essays")
            
            current_files = [file.name for file in uploaded_essays] if uploaded_essays else []
            if current_files != st.session_state["uploaded_files"]:
                st.session_state["uploaded_files"] = current_files
                st.session_state["grading_results"] = []
                
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
                                
                                upload_file_to_drive(service, doc_buffer.getvalue(), graded_filename, graded_essays_folder_id)
                        
                        st.session_state["grading_results"] = results
                    else:
                        st.error("Không tìm thấy đáp án mẫu trên Google Drive. Vui lòng tải lên đáp án trước.")

            if st.session_state["grading_results"]:
                df = pd.DataFrame(st.session_state["grading_results"])
                st.subheader("Kết quả chấm điểm hàng loạt:")
                st.dataframe(df)
                
                # Lưu file CSV với mã hóa utf-8-sig để tải về
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
                    for file in file_list:
                        file_content = download_file_from_drive(service, file['id'])
                        st.download_button(
                            label=f"Tải kết quả: {file['name']}",
                            data=file_content,
                            file_name=file['name'],
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        )
                else:
                    st.info("Chưa có kết quả chấm điểm nào được lưu.")
            elif uploaded_essays:
                st.info("Đang xử lý bài làm...")

        with tab3:
            df = load_grading_report(service, reports_folder_id)
            if df is not None:
                st.subheader("Báo cáo điểm tổng hợp:")
                st.dataframe(df)
                # Lưu file CSV với mã hóa utf-8-sig để tải về
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
        exam_file = find_file_in_folder(service, "de_thi.pdf", exams_folder_id)
        if exam_file:
            if "mssv" not in st.session_state:
                st.session_state["mssv"] = ""
            if "full_name" not in st.session_state:
                st.session_state["full_name"] = ""

            mssv = st.text_input("MSSV:", value=st.session_state["mssv"], key="mssv_input")
            full_name = st.text_input("Họ và Tên:", value=st.session_state["full_name"], key="full_name_input")

            st.session_state["mssv"] = mssv
            st.session_state["full_name"] = full_name

            if st.session_state["mssv"] and st.session_state["full_name"]:
                tab1, tab2 = st.tabs(["Làm bài thi online", "Nộp bài"])
                
                with tab1:
                    if "start_exam" not in st.session_state:
                        st.session_state["start_exam"] = False

                    if not st.session_state["start_exam"]:
                        if st.button("Làm bài"):
                            st.session_state["start_exam"] = True
                            st.session_state["current_num_questions"] = 1
                            st.rerun()
                    else:
                        st.subheader("Đề thi:")
                        exam_content = download_file_from_drive(service, exam_file['id'])
                        base64_pdf = get_base64_of_file(exam_content)
                        pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="600px" type="application/pdf"></iframe>'
                        st.markdown(pdf_display, unsafe_allow_html=True)
                        st.download_button(
                            label="Tải đề thi (PDF) nếu không xem được",
                            data=exam_content,
                            file_name="de_thi.pdf",
                            mime="application/pdf"
                        )
                        
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
                            filename = f"{st.session_state['mssv']}_{st.session_state['full_name']}.docx"
                            doc = docx.Document()
                            doc.add_paragraph(student_text)
                            doc_buffer = io.BytesIO()
                            doc.save(doc_buffer)
                            doc_buffer.seek(0)
                            
                            upload_file_to_drive(service, doc_buffer.getvalue(), filename, essays_folder_id)
                            st.success(f"Bài làm đã được lưu trên Google Drive với tên: {filename}")
                            st.session_state["start_exam"] = False
                            st.session_state["current_num_questions"] = 1
                            st.rerun()
                
                with tab2:
                    uploaded_essay = st.file_uploader("Tải lên bài làm tự luận", type=["docx"])
                    if uploaded_essay:
                        filename = f"{st.session_state['mssv']}_{st.session_state['full_name']}.docx"
                        essay_content = uploaded_essay.read()
                        upload_file_to_drive(service, essay_content, filename, essays_folder_id)
                        st.success(f"Bài làm đã được lưu trên Google Drive với tên: {filename}")
        else:
            st.error("Không tìm thấy đề thi trên Google Drive. Vui lòng liên hệ giáo viên để tải đề thi (PDF).")