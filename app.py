import streamlit as st
import requests
import docx
import pandas as pd
from streamlit_quill import st_quill
import base64
import io
import json
import re
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Khởi tạo các biến trạng thái
if "logged_in" not in st.session_state:
    st.session_state["logged_in"] = False
if "user" not in st.session_state:
    st.session_state["user"] = None
if "role" not in st.session_state:
    st.session_state["role"] = None
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

# Sử dụng OpenRouter API miễn phí
API_URL = "https://openrouter.ai/api/v1/chat/completions"
try:
    API_KEY = st.secrets["openrouter"]["api_key"]
except KeyError:
    st.error("Không tìm thấy API key của OpenRouter trong Secrets. Vui lòng thêm 'openrouter.api_key' vào Secrets trên Streamlit Cloud.")
    st.stop()

# Danh sách user giả lập
USERS = {
    "teacher": "1",
    "student": "1"
}

# Hàm thay đổi con trỏ chuột
def set_loading_cursor(status):
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

# Xác thực Google Drive
def authenticate_google_drive():
    SCOPES = ['https://www.googleapis.com/auth/drive']
    
    try:
        creds_info_str = st.secrets["google_drive"]["credentials"]
        client_secrets_str = st.secrets["google_drive"]["client_secrets"]
        creds_info = json.loads(creds_info_str)
        client_secrets = json.loads(client_secrets_str)
    except KeyError:
        error_msg = (
            "Không tìm thấy thông tin xác thực trong Secrets.\n"
            "Vui lòng thêm client_secrets và credentials vào Secrets trên Streamlit Cloud."
        )
        st.error(error_msg)
        st.stop()
    except json.JSONDecodeError as e:
        error_msg = (
            "Dữ liệu trong Secrets không đúng định dạng JSON.\n"
            f"Chi tiết lỗi: {str(e)}\n"
            "Vui lòng kiểm tra lại client_secrets và credentials trong Secrets trên Streamlit Cloud."
        )
        st.error(error_msg)
        st.stop()

    creds = None
    try:
        creds = Credentials.from_authorized_user_info(info=creds_info, scopes=SCOPES)
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                st.error(f"Lỗi khi làm mới token: {str(e)}")
                st.error("Vui lòng cập nhật credentials mới trong Secrets trên Streamlit Cloud.")
                st.stop()
    except Exception as e:
        st.error(f"Lỗi khi tạo credentials: {str(e)}")
        st.error("Vui lòng kiểm tra hoặc cập nhật credentials trong Secrets trên Streamlit Cloud.")
        st.stop()
    
    if not creds or not creds.valid:
        st.error("Credentials không hợp lệ. Vui lòng cập nhật credentials mới trong Secrets trên Streamlit Cloud.")
        st.stop()
    
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

# Tải file lên Google Drive và đặt quyền chia sẻ công khai
def upload_file_to_drive(service, file_content, file_name, folder_id):
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    media = MediaIoBaseUpload(io.BytesIO(file_content), mimetype='application/octet-stream')
    file = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    
    file_id = file['id']
    try:
        permission = {
            'type': 'anyone',
            'role': 'reader'
        }
        service.permissions().create(fileId=file_id, body=permission).execute()
    except Exception as e:
        st.error(f"Không thể đặt quyền chia sẻ công khai cho file {file_name}: {str(e)}")
        raise
    
    return file_id

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
except Exception as e:
    st.error(f"Lỗi khi khởi tạo Google Drive: {str(e)}")
    st.stop()

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
        <h2 style='text-align: center; font-size: 36px;'>👤Đăng nhập hệ thống</h2>
        """,
        unsafe_allow_html=True
    )
    user = st.text_input("Tên đăng nhập:")
    password = st.text_input("Mật khẩu:", type="password")
    if st.button("Đăng nhập", icon=":material/login:"):
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
    df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
    
    existing_file = find_file_in_folder(service, "grading_report.csv", folder_id)
    if existing_file:
        existing_content = download_file_from_drive(service, existing_file['id']).decode('utf-8-sig')
        existing_df = pd.read_csv(io.StringIO(existing_content), encoding='utf-8-sig')
        df = pd.concat([existing_df, df], ignore_index=True)
        df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        file_metadata = {'name': "grading_report.csv"}
        media = MediaIoBaseUpload(io.BytesIO(csv_buffer.getvalue().encode('utf-8')), mimetype='text/csv')
        service.files().update(fileId=existing_file['id'], body=file_metadata, media_body=media).execute()
    else:
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
        response = requests.post(API_URL, headers=headers, json=payload, timeout=30)
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
            error_detail = response.json() if response.content else "No response content"
            st.error(f"Lỗi API: {response.status_code} - {error_detail}")
            return None
    except requests.exceptions.Timeout:
        st.error("Yêu cầu API đã hết thời gian (timeout). Vui lòng thử lại sau.")
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

# Giao diện chính
if not st.session_state["logged_in"]:
    login()
else:
    st.markdown(
        "<h1 style='text-align: center; font-size: 40px;'>🎓Hệ thống chấm tự luận bằng AI</h1>",
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

        if uploaded_exam_pdf and uploaded_answer:
            exam_pdf_content = uploaded_exam_pdf.read()
            answer_content = uploaded_answer.read()
            
            upload_file_to_drive(service, exam_pdf_content, "de_thi.pdf", exams_folder_id)
            upload_file_to_drive(service, answer_content, "dap_an.docx", exams_folder_id)
            
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
                        
                        set_loading_cursor(True)
                        with st.spinner("Đang chấm bài..."):
                            result = grade_essay(student_text, answer_text, student_name, mssv)
                        
                        set_loading_cursor(False)
                        
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
            uploaded_essays = st.file_uploader("Tải lên nhiều bài làm tự luận", type=["docx"], accept_multiple_files=True, key="batch_essays")
            
            MAX_FILES = 10
            if uploaded_essays and len(uploaded_essays) > MAX_FILES:
                st.error(f"Vui lòng chỉ tải lên tối đa {MAX_FILES} file để chấm hàng loạt.")
                uploaded_essays = uploaded_essays[:MAX_FILES]
            
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
                                    
                                    graded_filename = f"{mssv}_{student_name}_graded.docx"
                                    doc = docx.Document()
                                    doc.add_paragraph(f"MSSV: {mssv}")
                                    doc.add_paragraph(f"Họ và Tên: {student_name}")
                                    doc.add_paragraph(grading_result)
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
                    for file in file_list:
                        set_loading_cursor(True)
                        with st.spinner(f"Đang tải file {file['name']}..."):
                            file_content = download_file_from_drive(service, file['id'])
                        set_loading_cursor(False)
                        
                        st.download_button(
                            label=f"Tải kết quả: {file['name']}",
                            data=file_content,
                            file_name=file['name'],
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key=f"download_{file['id']}"
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
            mssv = st.text_input("MSSV:", value=st.session_state["mssv"], key="mssv_input")
            full_name = st.text_input("Họ và Tên:", value=st.session_state["full_name"], key="full_name_input")

            st.session_state["mssv"] = mssv
            st.session_state["full_name"] = full_name

            if st.session_state["mssv"] and st.session_state["full_name"]:
                tab1, tab2 = st.tabs(["Làm bài thi online", "Nộp bài"])
                
                with tab1:
                    if not st.session_state["start_exam"]:
                        if st.button("Làm bài"):
                            st.session_state["start_exam"] = True
                            st.session_state["current_num_questions"] = 1
                            st.rerun()
                    else:
                        st.subheader("Đề thi:")
                        file_id = exam_file['id']
                        viewer_url = f"https://drive.google.com/viewerng/viewer?embedded=true&url=https://drive.google.com/uc?id={file_id}"
                        pdf_display = f'<iframe src="{viewer_url}" width="100%" height="600px" frameborder="0"></iframe>'
                        st.markdown(pdf_display, unsafe_allow_html=True)
                        st.info("Nếu đề thi không hiển thị, vui lòng sử dụng nút 'Tải đề thi (PDF) nếu không xem được' để tải file về và xem.")
                        
                        set_loading_cursor(True)
                        with st.spinner("Đang tải đề thi..."):
                            exam_content = download_file_from_drive(service, exam_file['id'])
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
