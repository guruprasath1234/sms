import sqlite3, json, hashlib, os, csv, io, uuid
from werkzeug.utils import secure_filename
from flask import Flask, request, jsonify, session, send_from_directory, make_response
from datetime import datetime, timedelta
import random
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors

app = Flask(__name__, static_folder='static')
app.secret_key = 'educore_secret_2024'
DB = 'educore.db'
DB_INITIALIZED = False
UPLOAD_FOLDER = 'uploads'
AVATAR_DIR = os.path.join(UPLOAD_FOLDER, 'avatars')
MATERIAL_DIR = os.path.join(UPLOAD_FOLDER, 'materials')
os.makedirs(AVATAR_DIR, exist_ok=True)
os.makedirs(MATERIAL_DIR, exist_ok=True)

# ─── DB INIT ──────────────────────────────────────────────────────────────────
def get_db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','teacher','student','parent')),
            phone TEXT,
            bio TEXT,
            avatar TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            student_id TEXT UNIQUE NOT NULL,
            course TEXT NOT NULL,
            year INTEGER DEFAULT 1,
            status TEXT DEFAULT 'Active' CHECK(status IN ('Active','Inactive')),
            enrolled_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            subject TEXT NOT NULL,
            experience INTEGER DEFAULT 1,
            classes TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS grades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER REFERENCES students(id),
            subject TEXT NOT NULL,
            mid_term REAL DEFAULT 0,
            final REAL DEFAULT 0,
            assignment REAL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            subject TEXT NOT NULL,
            description TEXT,
            due_date TEXT,
            status TEXT DEFAULT 'pending',
            score REAL,
            max_score REAL DEFAULT 100,
            url TEXT,
            teacher_id INTEGER REFERENCES teachers(id),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            subject TEXT,
            type TEXT,
            size TEXT,
            uploader TEXT,
            file_path TEXT,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER REFERENCES students(id),
            date TEXT NOT NULL,
            status TEXT DEFAULT 'Present' CHECK(status IN ('Present','Absent','Leave'))
        );

        CREATE TABLE IF NOT EXISTS fees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER REFERENCES students(id),
            total_amount REAL DEFAULT 0,
            amount_paid REAL DEFAULT 0,
            due_date TEXT,
            description TEXT,
            status TEXT DEFAULT 'Due' CHECK(status IN ('Paid','Due','Partial')),
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT,
            author TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS timetable (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_name TEXT NOT NULL,
            day TEXT NOT NULL,
            period INTEGER NOT NULL,
            subject TEXT,
            teacher TEXT,
            room TEXT
        );

        CREATE TABLE IF NOT EXISTS parents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            student_id INTEGER REFERENCES students(id),
            relationship TEXT DEFAULT 'Parent',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT,
            related_id INTEGER,
            is_read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assignment_id INTEGER REFERENCES assignments(id),
            student_id INTEGER REFERENCES students(id),
            file_path TEXT,
            submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
            is_late INTEGER DEFAULT 0,
            status TEXT DEFAULT 'submitted' CHECK(status IN ('draft','submitted','graded'))
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER REFERENCES submissions(id),
            teacher_id INTEGER REFERENCES teachers(id),
            score REAL,
            max_score REAL DEFAULT 100,
            comments TEXT,
            graded_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    # Migrate existing database to new schema fields where needed
    cols = [r['name'] for r in db.execute('PRAGMA table_info(materials)').fetchall()]
    if 'file_path' not in cols:
        db.execute('ALTER TABLE materials ADD COLUMN file_path TEXT')

    cols = [r['name'] for r in db.execute('PRAGMA table_info(assignments)').fetchall()]
    if 'url' not in cols:
        db.execute('ALTER TABLE assignments ADD COLUMN url TEXT')

    # Create new tables if not exist
    tables_to_create = ['parents', 'notifications', 'submissions', 'feedback']
    existing_tables = [r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    for table in tables_to_create:
        if table not in existing_tables:
            # Tables will be created by the initial executescript
            pass

    db.commit()
    _seed(db)
    db.close()

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def normalize_fee_values(total_amount, amount_paid):
    """Normalize fee values and derive status from balances."""
    total = max(float(total_amount or 0), 0.0)
    paid = max(float(amount_paid or 0), 0.0)
    # Avoid impossible states from manual edits.
    if paid > total:
        paid = total
    if total <= 0 or paid >= total:
        status = 'Paid'
    elif paid > 0:
        status = 'Partial'
    else:
        status = 'Due'
    return round(total, 2), round(paid, 2), status

def calculate_gpa(grades_list):
    """Calculate GPA from list of scores (0-100 scale converted to 4.0 scale)"""
    if not grades_list:
        return 0.0
    # Convert 0-100 to 4.0 GPA scale
    gpa_scores = []
    for g in grades_list:
        if g >= 90:
            gpa_scores.append(4.0)
        elif g >= 80:
            gpa_scores.append(3.5)
        elif g >= 70:
            gpa_scores.append(3.0)
        elif g >= 60:
            gpa_scores.append(2.5)
        elif g >= 50:
            gpa_scores.append(2.0)
        else:
            gpa_scores.append(0.0)
    return round(sum(gpa_scores) / len(gpa_scores), 2)

def get_academic_standing(gpa):
    """Determine academic standing based on GPA"""
    if gpa >= 3.8:
        return "Dean's List (Excellent)"
    elif gpa >= 3.5:
        return "Honor Roll"
    elif gpa >= 3.0:
        return "Good Standing"
    elif gpa >= 2.0:
        return "Regular Standing"
    elif gpa >= 1.5:
        return "Academic Probation"
    else:
        return "At-Risk"

def generate_report_card_pdf(student_name, student_id, course, year, gpa, grades_data):
    """Generate a PDF report card"""
    from io import BytesIO
    pdf_buffer = BytesIO()
    
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, topMargin=0.5*inch, bottomMargin=0.5*inch)
    story = []
    styles = getSampleStyleSheet()
    
    # Title
    title_style = ParagraphStyle('CustomTitle', parent=styles['Heading1'], fontSize=24, textColor=colors.HexColor('#003366'), spaceAfter=6)
    story.append(Paragraph("EduCore Student Report Card", title_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Student Info
    student_info = [
        ['Student Name:', student_name],
        ['Student ID:', student_id],
        ['Course:', course],
        ['Year:', str(year)],
        ['GPA:', f"{gpa:.2f}/4.0"],
        ['Academic Standing:', get_academic_standing(gpa)],
    ]
    
    table = Table(student_info, colWidths=[2*inch, 3*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#E8F0F7')),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.3*inch))
    
    # Grades Table
    story.append(Paragraph("Subject Grades", styles['Heading2']))
    story.append(Spacer(1, 0.1*inch))
    
    grades_table_data = [['Subject', 'Mid-Term', 'Final', 'Assignment', 'Average']]
    for grade in grades_data:
        avg = round((grade['mid_term'] + grade['final'] + grade['assignment']) / 3, 1)
        grades_table_data.append([
            grade['subject'],
            str(grade['mid_term']),
            str(grade['final']),
            str(grade['assignment']),
            str(avg)
        ])
    
    grades_table = Table(grades_table_data, colWidths=[1.5*inch, 1*inch, 1*inch, 1.2*inch, 1*inch])
    grades_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003366')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0F0F0')]),
    ]))
    story.append(grades_table)
    story.append(Spacer(1, 0.2*inch))
    
    # Footer
    footer_style = ParagraphStyle('Footer', parent=styles['Normal'], fontSize=8, textColor=colors.grey, alignment=1)
    story.append(Paragraph(f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | EduCore System", footer_style))
    
    doc.build(story)
    pdf_buffer.seek(0)
    return pdf_buffer

def _seed(db):
    # Skip if already seeded
    if db.execute('SELECT COUNT(*) FROM users').fetchone()[0] > 0:
        return

    courses = ['Computer Science','Mathematics','Physics','Chemistry','Biology','English']
    subjects = ['Mathematics','Physics','Chemistry','English','Computer Science']

    # Admin
    db.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
               ('Admin User','admin@educore.com', hash_pw('admin123'), 'admin'))

    # Teachers
    teacher_names = ['Dr. Sarah Johnson','Prof. Michael Chen','Ms. Emily Rodriguez','Mr. David Kim','Dr. Priya Sharma']
    teacher_subjects = ['Mathematics','Physics','Chemistry','Computer Science','English']
    for i,(n,s) in enumerate(zip(teacher_names, teacher_subjects)):
        uid = db.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
                         (n, f'teacher{i+1}@educore.com', hash_pw('teach123'), 'teacher')).lastrowid
        db.execute("INSERT INTO teachers(user_id,subject,experience,classes) VALUES(?,?,?,?)",
                   (uid, s, random.randint(3,15), 'Class 10-A, 10-B'))

    # Students
    first = ['Aarav','Zara','Rohan','Priya','Aiden','Mia','Arjun','Sofia','Vikram','Nadia',
             'Rahul','Emma','Kabir','Olivia','Dev','Ishaan','Ananya','Riya','Kiran','Sita']
    last  = ['Sharma','Patel','Singh','Kumar','Mehta','Gupta','Das','Rao','Nair','Pillai',
             'Verma','Shah','Joshi','Mishra','Reddy','Iyer','Bose','Malhotra','Chopra','Desai']
    for i in range(20):
        name = f'{first[i]} {last[i]}'
        uid  = db.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
                          (name, f'student{i+1}@educore.com', hash_pw('stud123'), 'student')).lastrowid
        sid  = db.execute("INSERT INTO students(user_id,student_id,course,year,status) VALUES(?,?,?,?,?)",
                          (uid, f'STU{1000+i}', random.choice(courses), random.randint(1,4),
                           random.choice(['Active','Active','Active','Inactive']))).lastrowid

        # Grades
        for subj in random.sample(subjects, 3):
            db.execute("INSERT INTO grades(student_id,subject,mid_term,final,assignment) VALUES(?,?,?,?,?)",
                       (sid, subj, round(random.uniform(50,95),1),
                        round(random.uniform(50,95),1), round(random.uniform(60,100),1)))

        # Attendance (last 30 days)
        for d in range(30):
            date = (datetime.now() - timedelta(days=d)).strftime('%Y-%m-%d')
            status = random.choices(['Present','Absent','Leave'], weights=[80,15,5])[0]
            db.execute("INSERT INTO attendance(student_id,date,status) VALUES(?,?,?)", (sid, date, status))

        # Fees
        total = round(random.uniform(1000, 5000), 2)
        paid = round(random.uniform(0, total), 2)
        fee_status = 'Paid' if paid >= total else ('Partial' if paid > 0 else 'Due')
        db.execute("INSERT INTO fees(student_id,total_amount,amount_paid,due_date,description,status) VALUES(?,?,?,?,?,?)",
                   (sid, total, paid, (datetime.now() + timedelta(days=random.randint(5,45))).strftime('%Y-%m-%d'), 'Tuition + materials', fee_status))

    # Assignments
    assign_data = [
        ('Calculus Problem Set', 'Mathematics', 'Solve integration problems', -2, 'graded', 87),
        ('Quantum Mechanics Lab', 'Physics', 'Lab report on wave functions', 5, 'pending', None),
        ('Organic Chemistry Essay', 'Chemistry', 'Write about polymers', -5, 'overdue', None),
        ('Python Data Structures', 'Computer Science', 'Implement linked list', 3, 'submitted', None),
        ('Shakespeare Analysis', 'English', 'Analyze Hamlet Act 3', 7, 'pending', None),
        ('Linear Algebra Quiz', 'Mathematics', 'Matrix operations test', -1, 'graded', 92),
        ('Thermodynamics Project', 'Physics', 'Heat engine efficiency', 10, 'pending', None),
    ]
    for t,s,d,delta,st,sc in assign_data:
        due = (datetime.now() + timedelta(days=delta)).strftime('%Y-%m-%d')
        db.execute("INSERT INTO assignments(title,subject,description,due_date,status,score) VALUES(?,?,?,?,?,?)",
                   (t,s,d,due,st,sc))

    # Materials
    mats = [
        ('Calculus Textbook Ch1-5','Mathematics','PDF','12.4 MB'),
        ('Physics Lab Manual','Physics','PDF','8.2 MB'),
        ('Intro to Python - Lecture','Computer Science','Video','245 MB'),
        ('Chemistry Formulas Sheet','Chemistry','Document','1.1 MB'),
        ('English Grammar Guide','English','Document','3.5 MB'),
        ('Data Structures Slides','Computer Science','PDF','5.8 MB'),
        ('Biology Diagrams Pack','Biology','Archive','18.9 MB'),
    ]
    for t,s,tp,sz in mats:
        db.execute("INSERT INTO materials(title,subject,type,size,uploader) VALUES(?,?,?,?,?)",
                   (t,s,tp,sz,'Faculty'))

    # Announcements
    anns = [
        ('Mid-Term Examinations Schedule', 'Mid-term exams will be held from April 10-20. Check your timetable for room allocations.', 'Admin'),
        ('Library Extended Hours', 'The library will remain open until 9 PM during the exam period.', 'Admin'),
        ('Annual Sports Day', 'Annual sports day is scheduled for April 25. Register with your house captain.', 'Faculty'),
        ('Holiday Notice', 'College will be closed on April 14 for Baisakhi. Enjoy the holiday!', 'Admin'),
    ]
    for t,c,a in anns:
        db.execute("INSERT INTO announcements(title,content,author) VALUES(?,?,?)", (t,c,a))

    # Timetable
    days = ['Monday','Tuesday','Wednesday','Thursday','Friday']
    subj_map = {1:'Mathematics',2:'Physics',3:'Chemistry',4:'English',5:'Computer Science',6:'PE'}
    colors = ['tt-math','tt-science','tt-chemistry','tt-english','tt-cs','tt-pe']
    for cls in ['Class 10-A','Class 10-B','Class 11-A','Class 12-A']:
        for day in days:
            for period in range(1,7):
                subj = random.choice(list(subj_map.values()))
                db.execute("INSERT INTO timetable(class_name,day,period,subject,teacher,room) VALUES(?,?,?,?,?,?)",
                           (cls, day, period, subj, random.choice(teacher_names), f'Room {random.randint(101,215)}'))

    # Parents (link to first 5 students)
    parent_names = ['John Smith', 'Sarah Johnson', 'Michael Brown', 'Emily Davis', 'David Wilson']
    student_ids = db.execute("SELECT id FROM students LIMIT 5").fetchall()
    for idx, (pname, stud) in enumerate(zip(parent_names, student_ids)):
        puid = db.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
                          (pname, f'parent{idx+1}@educore.com', hash_pw('parent123'), 'parent')).lastrowid
        db.execute("INSERT INTO parents(user_id,student_id,relationship) VALUES(?,?,?)",
                   (puid, stud['id'], 'Parent'))

    # Sample Submissions
    assignments_list = db.execute("SELECT id FROM assignments").fetchall()
    first_5_students = db.execute("SELECT id FROM students LIMIT 5").fetchall()
    for assign in assignments_list[:3]:
        for stud in first_5_students[:2]:
            db.execute("INSERT INTO submissions(assignment_id,student_id,file_path,is_late,status) VALUES(?,?,?,?,?)",
                       (assign['id'], stud['id'], f"materials/sample_{uuid.uuid4().hex}.pdf", 0, 'submitted'))

    # Sample Notifications
    first_3_students = db.execute("SELECT user_id FROM students LIMIT 3").fetchall()
    for stud in first_3_students:
        db.execute('''
            INSERT INTO notifications(user_id, type, title, message, is_read)
            VALUES(?, ?, ?, ?, ?)
        ''', (stud['user_id'], 'grade', 'New Grade Posted', 'Your Mathematics grade has been posted', 0))
        db.execute('''
            INSERT INTO notifications(user_id, type, title, message, is_read)
            VALUES(?, ?, ?, ?, ?)
        ''', (stud['user_id'], 'assignment', 'Assignment Due Soon', 'Calculus Problem Set is due in 2 days', 0))

    db.commit()

# ─── AUTH ──────────────────────────────────────────────────────────────────────
@app.before_request
def ensure_db_initialized():
    """Initialize schema/seed once even when launched via flask run/gunicorn."""
    global DB_INITIALIZED
    if DB_INITIALIZED:
        return
    init_db()
    DB_INITIALIZED = True

def require_auth(roles=None):
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                return jsonify({'error':'Unauthorized'}), 401
            if roles and session.get('role') not in roles:
                return jsonify({'error':'Forbidden'}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator

@app.route('/api/login', methods=['POST'])
def login():
    d = request.get_json(silent=True)
    if not d:
        d = request.form.to_dict()
    email = (d.get('email') or '').strip().lower() if d else ''
    password = d.get('password') if d else None
    if not email or not password:
        return jsonify({'error':'Email and password are required'}), 400

    db = get_db()
    u = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
    if not u:
        db.close()
        return jsonify({'error':'Invalid credentials'}), 401

    hashed_input = hash_pw(password)
    stored_password = u['password']
    if stored_password != hashed_input:
        # Support old plaintext passwords and auto-upgrade to hashed storage.
        if stored_password != password:
            db.close()
            return jsonify({'error':'Invalid credentials'}), 401
        db.execute('UPDATE users SET password=? WHERE id=?', (hashed_input, u['id']))
        db.commit()

    db.close()
    session['user_id'] = u['id']
    session['role'] = u['role']
    session['name'] = u['name']
    return jsonify({'id':u['id'],'name':u['name'],'email':u['email'],'role':u['role'],'avatar':u['avatar']})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok':True})

@app.route('/api/me')
def me():
    if 'user_id' not in session:
        return jsonify({'error':'Not logged in'}), 401
    db = get_db()
    u = db.execute('SELECT id,name,email,role,phone,bio,avatar FROM users WHERE id=?', (session['user_id'],)).fetchone()
    db.close()
    return jsonify(dict(u))

@app.route('/api/profile', methods=['PUT'])
@require_auth()
def update_profile():
    d = request.json
    db = get_db()
    db.execute('UPDATE users SET name=?,phone=?,bio=? WHERE id=?',
               (d.get('name'), d.get('phone'), d.get('bio'), session['user_id']))
    db.commit()
    db.close()
    session['name'] = d.get('name')
    return jsonify({'ok':True})

@app.route('/api/profile/avatar', methods=['POST'])
@require_auth()
def upload_avatar():
    if 'avatar' not in request.files:
        return jsonify({'error':'Missing avatar file'}), 400
    file = request.files['avatar']
    if file.filename == '':
        return jsonify({'error':'No file chosen'}), 400
    filename = secure_filename(file.filename)
    ext = filename.rsplit('.',1)[-1].lower() if '.' in filename else ''
    if ext not in ('png','jpg','jpeg','gif'):
        return jsonify({'error':'Invalid image type'}), 400
    unique = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(AVATAR_DIR, unique)
    file.save(filepath)
    avatar_url = f"/{UPLOAD_FOLDER}/avatars/{unique}"

    db = get_db()
    db.execute('UPDATE users SET avatar=? WHERE id=?', (avatar_url, session['user_id']))
    db.commit(); db.close()
    return jsonify({'ok':True, 'avatar': avatar_url})

@app.route('/uploads/<path:filename>')
def serve_uploads(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# ─── DASHBOARD ─────────────────────────────────────────────────────────────────
@app.route('/api/dashboard')
@require_auth()
def dashboard():
    db = get_db()
    stats = {
        'students': db.execute('SELECT COUNT(*) FROM students').fetchone()[0],
        'teachers': db.execute('SELECT COUNT(*) FROM teachers').fetchone()[0],
        'courses':  db.execute('SELECT COUNT(DISTINCT course) FROM students').fetchone()[0],
        'active':   db.execute("SELECT COUNT(*) FROM students WHERE status='Active'").fetchone()[0],
    }
    # Students per course
    courses = db.execute('SELECT course, COUNT(*) as cnt FROM students GROUP BY course').fetchall()
    # Attendance overview
    today = datetime.now().strftime('%Y-%m-%d')
    att = {
        'present': db.execute("SELECT COUNT(*) FROM attendance WHERE date=? AND status='Present'", (today,)).fetchone()[0],
        'absent':  db.execute("SELECT COUNT(*) FROM attendance WHERE date=? AND status='Absent'", (today,)).fetchone()[0],
        'leave':   db.execute("SELECT COUNT(*) FROM attendance WHERE date=? AND status='Leave'", (today,)).fetchone()[0],
    }
    # Top students by average grade
    top = db.execute('''
        SELECT u.name, s.student_id, s.course,
               ROUND(AVG((g.mid_term+g.final+g.assignment)/3),1) as avg
        FROM students s
        JOIN users u ON s.user_id=u.id
        JOIN grades g ON g.student_id=s.id
        GROUP BY s.id ORDER BY avg DESC LIMIT 5
    ''').fetchall()
    anns = db.execute('SELECT * FROM announcements ORDER BY created_at DESC LIMIT 4').fetchall()
    db.close()
    return jsonify({
        'stats': stats,
        'courses': [dict(r) for r in courses],
        'attendance': att,
        'top_students': [dict(r) for r in top],
        'announcements': [dict(r) for r in anns],
    })

# ─── STUDENTS ─────────────────────────────────────────────────────────────────
@app.route('/api/students', methods=['GET'])
@require_auth()
def get_students():
    db = get_db()
    base_filter = ''
    params = []
    if session.get('role') == 'student':
        base_filter = 'WHERE s.user_id = ?'
        params = [session['user_id']]
    elif session.get('role') == 'parent':
        parent = db.execute('SELECT student_id FROM parents WHERE user_id=?', (session['user_id'],)).fetchone()
        if parent:
            base_filter = 'WHERE s.id = ?'
            params = [parent['student_id']]
        else:
            db.close()
            return jsonify([])

    rows = db.execute(f'''
        SELECT s.id, s.student_id, u.name, s.course, s.status, s.year,
               u.email, u.phone,
               COALESCE((SELECT ROUND(AVG((g.mid_term+g.final+g.assignment)/3),1) FROM grades g WHERE g.student_id=s.id), 0) as avg_grade,
               COALESCE((SELECT ROUND(100.0*SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END)/MAX(1,COUNT(a.id)),1) FROM attendance a WHERE a.student_id=s.id), 0) as attendance,
               COALESCE((SELECT ROUND(SUM(total_amount - amount_paid),1) FROM fees WHERE student_id=s.id), 0) as fee_due,
               COALESCE((SELECT ROUND(SUM(amount_paid),1) FROM fees WHERE student_id=s.id), 0) as fee_paid
        FROM students s
        JOIN users u ON s.user_id=u.id
        {base_filter}
        ORDER BY u.name
    ''', params).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/fees', methods=['GET'])
@require_auth()
def get_fees():
    db = get_db()
    fee_select = '''
        SELECT f.*, s.student_id as student_code, u.name as student_name
        FROM fees f
        JOIN students s ON f.student_id = s.id
        JOIN users u ON s.user_id = u.id
    '''
    if session.get('role') == 'student':
        student = db.execute('SELECT id FROM students WHERE user_id=?', (session['user_id'],)).fetchone()
        if not student:
            db.close()
            return jsonify([])
        rows = db.execute(f"""{fee_select} WHERE f.student_id=? ORDER BY f.due_date""", (student['id'],)).fetchall()
    elif session.get('role') == 'parent':
        parent = db.execute('SELECT student_id FROM parents WHERE user_id=?', (session['user_id'],)).fetchone()
        if not parent:
            db.close()
            return jsonify([])
        rows = db.execute(f"""{fee_select} WHERE f.student_id=? ORDER BY f.due_date""", (parent['student_id'],)).fetchall()
    else:
        student_id = request.args.get('student_id', type=int)
        if student_id:
            rows = db.execute(f"""{fee_select} WHERE f.student_id=? ORDER BY f.due_date""", (student_id,)).fetchall()
        else:
            rows = db.execute(f"""{fee_select} ORDER BY f.due_date"""
                              ).fetchall()
    data = []
    for row in rows:
        fee = dict(row)
        total, paid, derived_status = normalize_fee_values(fee.get('total_amount'), fee.get('amount_paid'))
        fee['total_amount'] = total
        fee['amount_paid'] = paid
        fee['status'] = derived_status
        fee['due_amount'] = round(max(total - paid, 0.0), 2)
        data.append(fee)
    db.close()
    return jsonify(data)

@app.route('/api/fees', methods=['POST'])
@require_auth(['admin','teacher'])
def add_fee():
    d = request.json
    db = get_db()
    total, paid, status = normalize_fee_values(d.get('total_amount',0), d.get('amount_paid',0))
    db.execute('INSERT INTO fees(student_id,total_amount,amount_paid,due_date,description,status) VALUES(?,?,?,?,?,?)',
               (d['student_id'], total, paid, d.get('due_date'), d.get('description',''), status))
    db.commit(); db.close()
    return jsonify({'ok':True})

@app.route('/api/fees/<int:fid>', methods=['PUT'])
@require_auth(['admin','teacher'])
def update_fee(fid):
    d = request.json
    db = get_db()
    total, paid, status = normalize_fee_values(d.get('total_amount',0), d.get('amount_paid',0))
    db.execute('UPDATE fees SET total_amount=?,amount_paid=?,due_date=?,description=?,status=? WHERE id=?',
               (total, paid, d.get('due_date'), d.get('description',''), status, fid))
    db.commit(); db.close()
    return jsonify({'ok':True})

@app.route('/api/fees/<int:fid>/pay', methods=['POST'])
@require_auth(['admin','teacher','student','parent'])
def pay_fee(fid):
    d = request.json or {}
    payment = max(float(d.get('amount', 0) or 0), 0.0)
    if payment <= 0:
        return jsonify({'error':'Payment amount must be greater than 0'}), 400

    db = get_db()
    fee = db.execute('''
        SELECT f.id, f.student_id, f.total_amount, f.amount_paid
        FROM fees f
        WHERE f.id=?
    ''', (fid,)).fetchone()
    if not fee:
        db.close()
        return jsonify({'error':'Fee record not found'}), 404

    role = session.get('role')
    if role == 'student':
        student = db.execute('SELECT id FROM students WHERE user_id=?', (session['user_id'],)).fetchone()
        if not student or student['id'] != fee['student_id']:
            db.close()
            return jsonify({'error':'Forbidden'}), 403
    elif role == 'parent':
        parent = db.execute('SELECT student_id FROM parents WHERE user_id=?', (session['user_id'],)).fetchone()
        if not parent or parent['student_id'] != fee['student_id']:
            db.close()
            return jsonify({'error':'Forbidden'}), 403

    total = float(fee['total_amount'] or 0)
    current_paid = float(fee['amount_paid'] or 0)
    next_paid = min(current_paid + payment, total)
    total, paid, status = normalize_fee_values(total, next_paid)
    db.execute('UPDATE fees SET amount_paid=?, status=? WHERE id=?', (paid, status, fid))
    db.commit()
    db.close()
    return jsonify({'ok':True, 'amount_paid': paid, 'status': status, 'due_amount': round(max(total - paid, 0.0), 2)})

@app.route('/api/fees/<int:fid>', methods=['DELETE'])
@require_auth(['admin'])
def delete_fee(fid):
    db = get_db()
    db.execute('DELETE FROM fees WHERE id=?', (fid,))
    db.commit(); db.close()
    return jsonify({'ok':True})

@app.route('/api/assignments/<int:aid>/status', methods=['PUT'])
@require_auth(['admin','teacher','student'])
def update_assignment_status(aid):
    d = request.json
    new_status = d.get('status')
    if new_status not in ('pending','submitted','graded','overdue'):
        return jsonify({'error':'Invalid status'}), 400
    db = get_db()
    assignment = db.execute('SELECT id FROM assignments WHERE id=?', (aid,)).fetchone()
    if not assignment:
        db.close()
        return jsonify({'error':'Assignment not found'}), 404
    db.execute('UPDATE assignments SET status=? WHERE id=?', (new_status, aid))
    db.commit(); db.close()
    return jsonify({'ok':True})

@app.route('/api/students', methods=['POST'])
@require_auth(['admin','teacher'])
def add_student():
    d = request.json
    db = get_db()
    try:
        uid = db.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
                         (d['name'], d['email'], hash_pw(d.get('password','stud123')), 'student')).lastrowid
        db.execute("INSERT INTO students(user_id,student_id,course,year,status) VALUES(?,?,?,?,?)",
                   (uid, d['student_id'], d['course'], d.get('year',1), d.get('status','Active')))
        db.commit()
        db.close()
        return jsonify({'ok':True, 'message':'Student added successfully'})
    except sqlite3.IntegrityError as e:
        db.close()
        return jsonify({'error': str(e)}), 400

@app.route('/api/students/<int:sid>', methods=['PUT'])
@require_auth(['admin','teacher'])
def update_student(sid):
    d = request.json
    db = get_db()
    s = db.execute('SELECT user_id FROM students WHERE id=?',(sid,)).fetchone()
    if not s: return jsonify({'error':'Not found'}),404
    db.execute('UPDATE users SET name=?,email=? WHERE id=?',(d['name'],d['email'],s['user_id']))
    db.execute('UPDATE students SET course=?,year=?,status=? WHERE id=?',(d['course'],d.get('year',1),d['status'],sid))
    db.commit(); db.close()
    return jsonify({'ok':True})

@app.route('/api/students/<int:sid>', methods=['DELETE'])
@require_auth(['admin'])
def delete_student(sid):
    db = get_db()
    s = db.execute('SELECT user_id FROM students WHERE id=?',(sid,)).fetchone()
    if not s: return jsonify({'error':'Not found'}),404
    db.execute('DELETE FROM attendance WHERE student_id=?',(sid,))
    db.execute('DELETE FROM grades WHERE student_id=?',(sid,))
    db.execute('DELETE FROM students WHERE id=?',(sid,))
    db.execute('DELETE FROM users WHERE id=?',(s['user_id'],))
    db.commit(); db.close()
    return jsonify({'ok':True})

@app.route('/api/students/bulk-delete', methods=['POST'])
@require_auth(['admin'])
def bulk_delete_students():
    ids = request.json.get('ids',[])
    db = get_db()
    for sid in ids:
        s = db.execute('SELECT user_id FROM students WHERE id=?',(sid,)).fetchone()
        if s:
            db.execute('DELETE FROM attendance WHERE student_id=?',(sid,))
            db.execute('DELETE FROM grades WHERE student_id=?',(sid,))
            db.execute('DELETE FROM students WHERE id=?',(sid,))
            db.execute('DELETE FROM users WHERE id=?',(s['user_id'],))
    db.commit(); db.close()
    return jsonify({'ok':True})

# ─── TEACHERS ─────────────────────────────────────────────────────────────────
@app.route('/api/teachers', methods=['GET'])
@require_auth()
def get_teachers():
    db = get_db()
    rows = db.execute('''
        SELECT t.id, u.name, u.email, t.subject, t.experience, t.classes
        FROM teachers t JOIN users u ON t.user_id=u.id ORDER BY u.name
    ''').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/teachers', methods=['POST'])
@require_auth(['admin'])
def add_teacher():
    d = request.json
    db = get_db()
    try:
        uid = db.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
                         (d['name'],d['email'],hash_pw(d.get('password','teach123')),'teacher')).lastrowid
        db.execute("INSERT INTO teachers(user_id,subject,experience,classes) VALUES(?,?,?,?)",
                   (uid,d['subject'],d.get('experience',1),d.get('classes','')))
        db.commit(); db.close()
        return jsonify({'ok':True})
    except sqlite3.IntegrityError as e:
        db.close()
        return jsonify({'error':str(e)}),400

@app.route('/api/teachers/<int:tid>', methods=['DELETE'])
@require_auth(['admin'])
def delete_teacher(tid):
    db = get_db()
    t = db.execute('SELECT user_id FROM teachers WHERE id=?',(tid,)).fetchone()
    if not t: return jsonify({'error':'Not found'}),404
    db.execute('DELETE FROM teachers WHERE id=?',(tid,))
    db.execute('DELETE FROM users WHERE id=?',(t['user_id'],))
    db.commit(); db.close()
    return jsonify({'ok':True})

# ─── GRADES ───────────────────────────────────────────────────────────────────
@app.route('/api/grades', methods=['GET'])
@require_auth()
def get_grades():
    db = get_db()
    condition = ''
    params = []
    if session.get('role') == 'student':
        student = db.execute('SELECT id FROM students WHERE user_id=?', (session['user_id'],)).fetchone()
        if student:
            condition = 'WHERE g.student_id = ?'
            params = [student['id']]
        else:
            db.close()
            return jsonify([])
    elif session.get('role') == 'parent':
        parent = db.execute('SELECT student_id FROM parents WHERE user_id=?', (session['user_id'],)).fetchone()
        if parent:
            condition = 'WHERE g.student_id = ?'
            params = [parent['student_id']]
        else:
            db.close()
            return jsonify([])
    rows = db.execute(f'''
        SELECT g.id, u.name as student_name, s.student_id, g.subject,
               g.mid_term, g.final, g.assignment,
               ROUND((g.mid_term+g.final+g.assignment)/3,1) as total
        FROM grades g
        JOIN students s ON g.student_id=s.id
        JOIN users u ON s.user_id=u.id
        {condition}
        ORDER BY u.name
    ''', params).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/grades', methods=['POST'])
@require_auth(['admin','teacher'])
def add_grade():
    d = request.json
    db = get_db()
    # find student by student_id string
    s = db.execute("SELECT id FROM students WHERE student_id=?", (d['student_id'],)).fetchone()
    if not s: return jsonify({'error':'Student not found'}),404
    db.execute("INSERT INTO grades(student_id,subject,mid_term,final,assignment) VALUES(?,?,?,?,?)",
               (s['id'],d['subject'],d.get('mid_term',0),d.get('final',0),d.get('assignment',0)))
    db.commit(); db.close()
    return jsonify({'ok':True})

@app.route('/api/grades/<int:gid>', methods=['PUT'])
@require_auth(['admin','teacher'])
def update_grade(gid):
    d = request.json
    db = get_db()
    db.execute("UPDATE grades SET mid_term=?,final=?,assignment=? WHERE id=?",
               (d.get('mid_term',0),d.get('final',0),d.get('assignment',0),gid))
    db.commit(); db.close()
    return jsonify({'ok':True})

@app.route('/api/grades/<int:gid>', methods=['DELETE'])
@require_auth(['admin','teacher'])
def delete_grade(gid):
    db = get_db()
    db.execute('DELETE FROM grades WHERE id=?',(gid,))
    db.commit(); db.close()
    return jsonify({'ok':True})

# ─── ASSIGNMENTS ──────────────────────────────────────────────────────────────
@app.route('/api/assignments', methods=['GET'])
@require_auth()
def get_assignments():
    db = get_db()
    rows = db.execute('SELECT * FROM assignments ORDER BY due_date').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/assignments', methods=['POST'])
@require_auth(['admin','teacher'])
def add_assignment():
    d = request.json
    db = get_db()
    db.execute("INSERT INTO assignments(title,subject,description,due_date,status,max_score,url) VALUES(?,?,?,?,?,?,?)",
               (d['title'],d['subject'],d.get('description',''),d['due_date'],d.get('status','pending'),d.get('max_score',100), d.get('url','')))
    db.commit(); db.close()
    return jsonify({'ok':True})

@app.route('/api/assignments/<int:aid>', methods=['DELETE'])
@require_auth(['admin','teacher'])
def delete_assignment(aid):
    db = get_db()
    db.execute('DELETE FROM assignments WHERE id=?',(aid,))
    db.commit(); db.close()
    return jsonify({'ok':True})

# ─── MATERIALS ────────────────────────────────────────────────────────────────
@app.route('/api/materials', methods=['GET'])
@require_auth()
def get_materials():
    db = get_db()
    rows = db.execute('SELECT * FROM materials ORDER BY uploaded_at DESC').fetchall()
    db.close()
    materials = []
    for r in rows:
        m = dict(r)
        m['download_url'] = f"/api/materials/download/{m['id']}"
        materials.append(m)
    return jsonify(materials)

@app.route('/api/materials/upload', methods=['POST'])
@require_auth(['admin','teacher'])
def upload_material():
    if 'file' not in request.files:
        return jsonify({'error':'File is required'}), 400
    file = request.files['file']
    if file.filename=='':
        return jsonify({'error':'No file selected'}),400
    filename = secure_filename(file.filename)
    unique = f"{uuid.uuid4().hex}_{filename}"
    save_path = os.path.join(MATERIAL_DIR, unique)
    file.save(save_path)
    size = request.form.get('size') or f"{os.path.getsize(save_path)/1024/1024:.2f} MB"
    material_type = request.form.get('type') or filename.rsplit('.',1)[-1].upper()
    title = request.form.get('title') or filename
    subject = request.form.get('subject', '')
    file_path = f"materials/{unique}"

    db = get_db()
    db.execute("INSERT INTO materials(title,subject,type,size,uploader,file_path) VALUES(?,?,?,?,?,?)",
               (title, subject, material_type, size, session['name'], file_path))
    db.commit(); db.close()
    return jsonify({'ok':True})

@app.route('/api/materials/download/<int:mid>')
@require_auth()
def download_material(mid):
    db = get_db()
    m = db.execute('SELECT file_path,title FROM materials WHERE id=?', (mid,)).fetchone()
    db.close()
    if not m or not m['file_path']:
        return jsonify({'error':'Material file not found'}), 404
    material_path = m['file_path']
    # expected like materials/filename.ext
    if material_path.startswith('materials/'):
        filename = material_path.split('/',1)[1]
        return send_from_directory(MATERIAL_DIR, filename, as_attachment=True, download_name=m['title'])
    return jsonify({'error':'Invalid file path'}), 500

@app.route('/api/materials', methods=['POST'])
@require_auth(['admin','teacher'])
def add_material():
    d = request.json
    db = get_db()
    db.execute("INSERT INTO materials(title,subject,type,size,uploader) VALUES(?,?,?,?,?)",
               (d['title'],d.get('subject',''),d.get('type','Document'),d.get('size','—'),session['name']))
    db.commit(); db.close()
    return jsonify({'ok':True})

@app.route('/api/materials/<int:mid>', methods=['DELETE'])
@require_auth(['admin','teacher'])
def delete_material(mid):
    db = get_db()
    db.execute('DELETE FROM materials WHERE id=?',(mid,))
    db.commit(); db.close()
    return jsonify({'ok':True})

# ─── ATTENDANCE ───────────────────────────────────────────────────────────────
@app.route('/api/attendance', methods=['GET'])
@require_auth()
def get_attendance():
    db = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    rows = db.execute('''
        SELECT s.id as student_id, u.name, s.student_id as sid,
               COALESCE(a.status,'Present') as status
        FROM students s
        JOIN users u ON s.user_id=u.id
        LEFT JOIN attendance a ON a.student_id=s.id AND a.date=?
        ORDER BY u.name
    ''', (today,)).fetchall()
    # Summary
    summary = db.execute('''
        SELECT s.id, u.name,
               ROUND(100.0*SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END)/MAX(1,COUNT(a.id)),1) as pct
        FROM students s JOIN users u ON s.user_id=u.id
        LEFT JOIN attendance a ON a.student_id=s.id
        GROUP BY s.id ORDER BY pct
    ''').fetchall()
    db.close()
    return jsonify({'today': [dict(r) for r in rows], 'summary': [dict(r) for r in summary]})

@app.route('/api/attendance', methods=['POST'])
@require_auth(['admin','teacher'])
def save_attendance():
    records = request.json  # [{student_id, status}]
    today = datetime.now().strftime('%Y-%m-%d')
    db = get_db()
    for r in records:
        existing = db.execute('SELECT id FROM attendance WHERE student_id=? AND date=?',
                              (r['student_id'], today)).fetchone()
        if existing:
            db.execute('UPDATE attendance SET status=? WHERE id=?',(r['status'],existing['id']))
        else:
            db.execute('INSERT INTO attendance(student_id,date,status) VALUES(?,?,?)',
                       (r['student_id'],today,r['status']))
    db.commit(); db.close()
    return jsonify({'ok':True})

# ─── ANNOUNCEMENTS ────────────────────────────────────────────────────────────
@app.route('/api/announcements', methods=['GET'])
@require_auth()
def get_announcements():
    db = get_db()
    rows = db.execute('SELECT * FROM announcements ORDER BY created_at DESC').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/announcements', methods=['POST'])
@require_auth(['admin','teacher'])
def add_announcement():
    d = request.json
    db = get_db()
    db.execute("INSERT INTO announcements(title,content,author) VALUES(?,?,?)",
               (d['title'],d.get('content',''),session['name']))
    db.commit(); db.close()
    return jsonify({'ok':True})

@app.route('/api/announcements/<int:aid>', methods=['DELETE'])
@require_auth(['admin'])
def delete_announcement(aid):
    db = get_db()
    db.execute('DELETE FROM announcements WHERE id=?',(aid,))
    db.commit(); db.close()
    return jsonify({'ok':True})

# ─── TIMETABLE ────────────────────────────────────────────────────────────────
@app.route('/api/timetable', methods=['GET'])
@require_auth()
def get_timetable():
    cls = request.args.get('class','Class 10-A')
    db = get_db()
    rows = db.execute('SELECT * FROM timetable WHERE class_name=? ORDER BY period',
                      (cls,)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/timetable', methods=['POST'])
@require_auth(['admin','teacher'])
def add_timetable():
    d = request.json
    db = get_db()
    db.execute("INSERT INTO timetable(class_name,day,period,subject,teacher,room) VALUES(?,?,?,?,?,?)",
               (d['class_name'],d['day'],d['period'],d['subject'],d.get('teacher',''),d.get('room','')))
    db.commit(); db.close()
    return jsonify({'ok':True})

# ─── EXPORT ───────────────────────────────────────────────────────────────────
@app.route('/api/export/students/csv')
@require_auth()
def export_csv():
    db = get_db()
    rows = db.execute('''
        SELECT s.student_id, u.name, u.email, s.course, s.year, s.status,
               ROUND(AVG((g.mid_term+g.final+g.assignment)/3),1) as avg_grade
        FROM students s JOIN users u ON s.user_id=u.id
        LEFT JOIN grades g ON g.student_id=s.id
        GROUP BY s.id ORDER BY u.name
    ''').fetchall()
    db.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Student ID','Name','Email','Course','Year','Status','Avg Grade'])
    for r in rows: writer.writerow(list(r))
    resp = make_response(output.getvalue())
    resp.headers['Content-Type'] = 'text/csv'
    resp.headers['Content-Disposition'] = 'attachment; filename=students.csv'
    return resp

# ─── FEATURE 1: PERFORMANCE ANALYTICS ──────────────────────────────────────────
@app.route('/api/analytics/grade-trends/<int:student_id>')
@require_auth()
def get_grade_trends(student_id):
    """Get grade trends for a specific student"""
    db = get_db()
    trends = db.execute('''
        SELECT subject, 
               ROUND(AVG(mid_term), 1) as mid_avg,
               ROUND(AVG(final), 1) as final_avg,
               ROUND(AVG(assignment), 1) as assign_avg,
               ROUND(AVG((mid_term+final+assignment)/3), 1) as overall_avg,
               COUNT(*) as records
        FROM grades WHERE student_id=? 
        GROUP BY subject
    ''', (student_id,)).fetchall()
    db.close()
    return jsonify([dict(r) for r in trends])

@app.route('/api/analytics/subject-performance')
@require_auth()
def get_subject_performance():
    """Get class-wide subject performance statistics"""
    db = get_db()
    performance = db.execute('''
        SELECT subject,
               ROUND(AVG((mid_term+final+assignment)/3), 1) as class_avg,
               ROUND(MIN((mid_term+final+assignment)/3), 1) as min_score,
               ROUND(MAX((mid_term+final+assignment)/3), 1) as max_score,
               COUNT(*) as student_count
        FROM grades GROUP BY subject ORDER BY class_avg DESC
    ''').fetchall()
    db.close()
    return jsonify([dict(r) for r in performance])

@app.route('/api/analytics/at-risk-students')
@require_auth(['admin','teacher'])
def get_at_risk_students():
    """Identify students at risk (avg grade < 60 or low attendance)"""
    db = get_db()
    # Students with low grades
    low_grades = db.execute('''
        SELECT DISTINCT s.id, u.name, s.student_id, s.course,
               ROUND(AVG((g.mid_term+g.final+g.assignment)/3), 1) as avg_grade
        FROM students s
        JOIN users u ON s.user_id=u.id
        LEFT JOIN grades g ON g.student_id=s.id
        GROUP BY s.id HAVING avg_grade < 60 OR avg_grade IS NULL
    ''').fetchall()
    
    # Students with low attendance
    low_attendance = db.execute('''
        SELECT DISTINCT s.id, u.name, s.student_id, s.course,
               ROUND(100.0*SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END)/MAX(1,COUNT(a.id)), 1) as attendance_pct
        FROM students s
        JOIN users u ON s.user_id=u.id
        LEFT JOIN attendance a ON a.student_id=s.id
        GROUP BY s.id HAVING attendance_pct < 75
    ''').fetchall()
    
    db.close()
    return jsonify({
        'low_grades': [dict(r) for r in low_grades],
        'low_attendance': [dict(r) for r in low_attendance]
    })

@app.route('/api/analytics/class-statistics')
@require_auth()
def get_class_statistics():
    """Get class-wide performance statistics"""
    db = get_db()
    stats = {
        'total_students': db.execute('SELECT COUNT(*) FROM students').fetchone()[0],
        'avg_grade': db.execute('SELECT ROUND(AVG((mid_term+final+assignment)/3), 1) FROM grades').fetchone()[0] or 0,
        'avg_attendance': db.execute('SELECT ROUND(100.0*SUM(CASE WHEN status=\'Present\' THEN 1 ELSE 0 END)/MAX(1,COUNT(*)), 1) FROM attendance').fetchone()[0] or 0,
        'pass_rate': db.execute('SELECT ROUND(100.0*SUM(CASE WHEN (mid_term+final+assignment)/3 >= 60 THEN 1 ELSE 0 END)/MAX(1,COUNT(DISTINCT student_id)), 1) FROM grades').fetchone()[0] or 0,
    }
    db.close()
    return jsonify(stats)

@app.route('/api/analytics/student-strength/<int:student_id>')
@require_auth()
def get_student_strength_weakness(student_id):
    """Identify student's strength and weakness subjects"""
    db = get_db()
    subjects = db.execute('''
        SELECT subject, ROUND(AVG((mid_term+final+assignment)/3), 1) as avg
        FROM grades WHERE student_id=?
        GROUP BY subject ORDER BY avg DESC
    ''', (student_id,)).fetchall()
    db.close()
    subjects_list = [dict(r) for r in subjects]
    return jsonify({
        'strength': subjects_list[:2] if len(subjects_list) >= 2 else subjects_list,
        'weakness': subjects_list[-2:] if len(subjects_list) >= 2 else subjects_list,
        'all_subjects': subjects_list
    })

# ─── FEATURE 2: PARENT PORTAL ──────────────────────────────────────────────────
@app.route('/api/parents/register', methods=['POST'])
def register_parent():
    """Register a new parent and link to student"""
    d = request.json
    db = get_db()
    try:
        # Create parent user account
        uid = db.execute("INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
                         (d['name'], d['email'], hash_pw(d.get('password','parent123')), 'parent')).lastrowid
        # Link parent to student
        student = db.execute("SELECT id FROM students WHERE student_id=?", (d['student_id'],)).fetchone()
        if not student:
            db.close()
            return jsonify({'error': 'Student not found'}), 404
        db.execute("INSERT INTO parents(user_id,student_id,relationship) VALUES(?,?,?)",
                   (uid, student['id'], d.get('relationship', 'Parent')))
        db.commit()
        db.close()
        return jsonify({'ok': True, 'parent_id': uid})
    except sqlite3.IntegrityError as e:
        db.close()
        return jsonify({'error': str(e)}), 400

@app.route('/api/parents/my-child')
@require_auth()
def get_my_child():
    """Get child's information for parent"""
    if session.get('role') != 'parent':
        return jsonify({'error': 'Only parents can access this'}), 403
    
    db = get_db()
    parent = db.execute("SELECT student_id FROM parents WHERE user_id=?", (session['user_id'],)).fetchone()
    if not parent:
        db.close()
        return jsonify({'error': 'No student linked to this parent'}), 404
    
    child = db.execute('''
        SELECT s.id, s.student_id, u.name, s.course, s.year, s.status,
               ROUND(AVG((g.mid_term+g.final+g.assignment)/3), 1) as avg_grade,
               ROUND(100.0*SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END)/MAX(1,COUNT(a.id)), 1) as attendance
        FROM students s
        JOIN users u ON s.user_id=u.id
        LEFT JOIN grades g ON g.student_id=s.id
        LEFT JOIN attendance a ON a.student_id=s.id
        WHERE s.id=?
        GROUP BY s.id
    ''', (parent['student_id'],)).fetchone()
    
    # Get child's grades
    grades = db.execute('''
        SELECT subject, mid_term, final, assignment, ROUND((mid_term+final+assignment)/3, 1) as avg
        FROM grades WHERE student_id=?
    ''', (parent['student_id'],)).fetchall()
    
    # Get recent attendance
    attendance = db.execute('''
        SELECT date, status FROM attendance 
        WHERE student_id=? 
        ORDER BY date DESC LIMIT 30
    ''', (parent['student_id'],)).fetchall()
    
    db.close()
    return jsonify({
        'child': dict(child),
        'grades': [dict(r) for r in grades],
        'recent_attendance': [dict(r) for r in attendance]
    })

@app.route('/api/parents/child-assignments')
@require_auth()
def get_parent_child_assignments():
    """Get child's assignments for parent"""
    if session.get('role') != 'parent':
        return jsonify({'error': 'Only parents can access this'}), 403
    
    db = get_db()
    parent = db.execute("SELECT student_id FROM parents WHERE user_id=?", (session['user_id'],)).fetchone()
    if not parent:
        db.close()
        return jsonify({'error': 'No student linked'}), 404
    
    assignments = db.execute('''
        SELECT a.*, COALESCE(f.score, 'Not graded') as score, f.comments
        FROM assignments a
        LEFT JOIN submissions s ON a.id=s.assignment_id AND s.student_id=?
        LEFT JOIN feedback f ON s.id=f.submission_id
        WHERE a.status != 'pending' OR s.id IS NOT NULL
        ORDER BY a.due_date DESC
    ''', (parent['student_id'],)).fetchall()
    
    db.close()
    return jsonify([dict(r) for r in assignments])

# ─── FEATURE 3: NOTIFICATIONS ──────────────────────────────────────────────────
@app.route('/api/notifications', methods=['GET'])
@require_auth()
def get_notifications():
    """Get user's notifications"""
    db = get_db()
    limit = request.args.get('limit', 20, type=int)
    notifs = db.execute('''
        SELECT * FROM notifications 
        WHERE user_id=?
        ORDER BY created_at DESC LIMIT ?
    ''', (session['user_id'], limit)).fetchall()
    db.close()
    return jsonify([dict(r) for r in notifs])

@app.route('/api/notifications/unread-count')
@require_auth()
def get_unread_count():
    """Get count of unread notifications"""
    db = get_db()
    count = db.execute('''
        SELECT COUNT(*) FROM notifications 
        WHERE user_id=? AND is_read=0
    ''', (session['user_id'],)).fetchone()[0]
    db.close()
    return jsonify({'unread': count})

@app.route('/api/notifications/<int:notif_id>/read', methods=['PUT'])
@require_auth()
def mark_notification_read(notif_id):
    """Mark notification as read"""
    db = get_db()
    db.execute('UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?', (notif_id, session['user_id']))
    db.commit()
    db.close()
    return jsonify({'ok': True})

@app.route('/api/notifications/create', methods=['POST'])
@require_auth(['admin','teacher'])
def create_notification():
    """Create a notification"""
    d = request.json
    db = get_db()
    db.execute('''
        INSERT INTO notifications(user_id, type, title, message, related_id)
        VALUES(?, ?, ?, ?, ?)
    ''', (d['user_id'], d['type'], d['title'], d.get('message', ''), d.get('related_id')))
    db.commit()
    db.close()
    return jsonify({'ok': True})

@app.route('/api/notifications/broadcast', methods=['POST'])
@require_auth(['admin'])
def broadcast_notification():
    """Broadcast notification to multiple users"""
    d = request.json
    db = get_db()
    user_ids = d.get('user_ids', [])
    for uid in user_ids:
        db.execute('''
            INSERT INTO notifications(user_id, type, title, message)
            VALUES(?, ?, ?, ?)
        ''', (uid, d['type'], d['title'], d.get('message', '')))
    db.commit()
    db.close()
    return jsonify({'ok': True})

# ─── FEATURE 4: ASSIGNMENT SUBMISSIONS & REVIEW ──────────────────────────────
@app.route('/api/submissions/upload', methods=['POST'])
@require_auth(['student'])
def upload_submission():
    """Student submits assignment"""
    assignment_id = request.form.get('assignment_id', type=int)
    if 'file' not in request.files:
        return jsonify({'error': 'File is required'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    db = get_db()
    # Get student ID
    student = db.execute("SELECT id FROM students WHERE user_id=?", (session['user_id'],)).fetchone()
    if not student:
        db.close()
        return jsonify({'error': 'Student not found'}), 404
    
    # Check if assignment exists
    assignment = db.execute("SELECT due_date FROM assignments WHERE id=?", (assignment_id,)).fetchone()
    if not assignment:
        db.close()
        return jsonify({'error': 'Assignment not found'}), 404
    
    # Save file
    filename = secure_filename(file.filename)
    unique = f"{uuid.uuid4().hex}_{filename}"
    save_path = os.path.join(MATERIAL_DIR, unique)
    file.save(save_path)
    
    # Check if late
    is_late = 1 if datetime.now().strftime('%Y-%m-%d') > assignment['due_date'] else 0
    
    # Save submission
    db.execute('''
        INSERT INTO submissions(assignment_id, student_id, file_path, is_late, status)
        VALUES(?, ?, ?, ?, ?)
    ''', (assignment_id, student['id'], f"materials/{unique}", is_late, 'submitted'))
    
    db.commit()
    db.close()
    
    # Create notification for teacher
    db = get_db()
    teachers = db.execute("SELECT DISTINCT user_id FROM teachers").fetchall()
    for t in teachers:
        db.execute('''
            INSERT INTO notifications(user_id, type, title, message, related_id)
            VALUES(?, ?, ?, ?, ?)
        ''', (t['user_id'], 'assignment', 'New Submission', f'A student submitted assignment {assignment_id}', assignment_id))
    db.commit()
    db.close()
    
    return jsonify({'ok': True, 'is_late': is_late})

@app.route('/api/submissions/<int:assignment_id>')
@require_auth()
def get_submissions(assignment_id):
    """Get submissions for an assignment"""
    db = get_db()
    submissions = db.execute('''
        SELECT s.id, u.name, st.student_id, s.submitted_at, s.is_late,
               COALESCE(f.score, NULL) as score, f.comments
        FROM submissions s
        JOIN students st ON s.student_id=st.id
        JOIN users u ON st.user_id=u.id
        LEFT JOIN feedback f ON s.id=f.submission_id
        WHERE s.assignment_id=?
        ORDER BY s.submitted_at DESC
    ''', (assignment_id,)).fetchall()
    db.close()
    return jsonify([dict(r) for r in submissions])

@app.route('/api/submissions/<int:submission_id>/feedback', methods=['POST'])
@require_auth(['teacher', 'admin'])
def add_feedback(submission_id):
    """Teacher adds feedback and grade to submission"""
    d = request.json
    db = get_db()
    
    # Get teacher ID
    teacher = db.execute("SELECT id FROM teachers WHERE user_id=?", (session['user_id'],)).fetchone()
    if not teacher:
        db.close()
        return jsonify({'error': 'Teacher not found'}), 403
    
    # Check if feedback already exists
    existing = db.execute("SELECT id FROM feedback WHERE submission_id=?", (submission_id,)).fetchone()
    if existing:
        db.execute('''
            UPDATE feedback SET score=?, max_score=?, comments=?, graded_at=?
            WHERE submission_id=?
        ''', (d.get('score'), d.get('max_score', 100), d.get('comments', ''), datetime.now().isoformat(), submission_id))
    else:
        db.execute('''
            INSERT INTO feedback(submission_id, teacher_id, score, max_score, comments)
            VALUES(?, ?, ?, ?, ?)
        ''', (submission_id, teacher['id'], d.get('score'), d.get('max_score', 100), d.get('comments', '')))
    
    # Update submission status
    db.execute("UPDATE submissions SET status='graded' WHERE id=?", (submission_id,))
    
    db.commit()
    db.close()
    
    return jsonify({'ok': True})

@app.route('/api/submissions/<int:submission_id>/download')
@require_auth()
def download_submission(submission_id):
    """Download submitted assignment"""
    db = get_db()
    submission = db.execute('''
        SELECT s.file_path, a.title FROM submissions s
        JOIN assignments a ON s.assignment_id=a.id
        WHERE s.id=?
    ''', (submission_id,)).fetchone()
    db.close()
    
    if not submission or not submission['file_path']:
        return jsonify({'error': 'File not found'}), 404
    
    if submission['file_path'].startswith('materials/'):
        filename = submission['file_path'].split('/', 1)[1]
        return send_from_directory(MATERIAL_DIR, filename, as_attachment=True, download_name=f"{submission['title']}.pdf")
    
    return jsonify({'error': 'Invalid file path'}), 500

# ─── FEATURE 6: REPORT CARD GENERATION ─────────────────────────────────────────
@app.route('/api/reports/report-card/<int:student_id>')
@require_auth()
def generate_report_card(student_id):
    """Generate PDF report card for a student"""
    db = get_db()
    
    student = db.execute('''
        SELECT u.name, s.student_id, s.course, s.year FROM students s
        JOIN users u ON s.user_id=u.id WHERE s.id=?
    ''', (student_id,)).fetchone()
    
    if not student:
        db.close()
        return jsonify({'error': 'Student not found'}), 404
    
    grades = db.execute('''
        SELECT subject, mid_term, final, assignment FROM grades WHERE student_id=?
    ''', (student_id,)).fetchall()
    
    db.close()
    
    # Calculate GPA
    grade_values = [round((g['mid_term'] + g['final'] + g['assignment']) / 3, 1) for g in grades]
    gpa = calculate_gpa(grade_values)
    
    # Generate PDF
    pdf_buffer = generate_report_card_pdf(
        student['name'],
        student['student_id'],
        student['course'],
        student['year'],
        gpa,
        [dict(g) for g in grades]
    )
    
    response = make_response(pdf_buffer.getvalue())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=report_card_{student["student_id"]}.pdf'
    return response

# ─── FEATURE 10: GPA CALCULATOR & ACADEMIC STANDING ─────────────────────────────
@app.route('/api/gpa/student/<int:student_id>')
@require_auth()
def get_student_gpa(student_id):
    """Get student's GPA and academic standing"""
    db = get_db()
    
    student = db.execute('''
        SELECT u.name, s.student_id, s.course, s.year FROM students s
        JOIN users u ON s.user_id=u.id WHERE s.id=?
    ''', (student_id,)).fetchone()
    
    if not student:
        db.close()
        return jsonify({'error': 'Student not found'}), 404
    
    # Get all grades
    grades = db.execute('''
        SELECT subject, mid_term, final, assignment FROM grades WHERE student_id=?
    ''', (student_id,)).fetchall()
    
    db.close()
    
    grade_values = [round((g['mid_term'] + g['final'] + g['assignment']) / 3, 1) for g in grades]
    gpa = calculate_gpa(grade_values)
    standing = get_academic_standing(gpa)
    
    return jsonify({
        'student_name': student['name'],
        'student_id': student['student_id'],
        'gpa': gpa,
        'academic_standing': standing,
        'subject_count': len(grades),
        'average_grade': round(sum(grade_values) / len(grade_values), 1) if grade_values else 0
    })

@app.route('/api/gpa/class-rankings')
@require_auth()
def get_class_rankings():
    """Get GPA-based class rankings"""
    db = get_db()
    
    rankings = db.execute('''
        SELECT s.id, u.name, s.student_id, s.course,
               ROUND(AVG((g.mid_term+g.final+g.assignment)/3), 1) as avg_grade
        FROM students s
        JOIN users u ON s.user_id=u.id
        LEFT JOIN grades g ON g.student_id=s.id
        WHERE s.status='Active'
        GROUP BY s.id
        ORDER BY avg_grade DESC
    ''').fetchall()
    
    db.close()
    
    result = []
    for idx, rank in enumerate(rankings, 1):
        grade_val = rank['avg_grade'] or 0
        gpa = calculate_gpa([grade_val])
        result.append({
            'rank': idx,
            'name': rank['name'],
            'student_id': rank['student_id'],
            'course': rank['course'],
            'average_grade': grade_val,
            'gpa': gpa,
            'standing': get_academic_standing(gpa)
        })
    
    return jsonify(result)

@app.route('/api/gpa/academic-standing-report')
@require_auth(['admin', 'teacher'])
def get_academic_standing_report():
    """Get report of all students by academic standing"""
    db = get_db()
    
    students = db.execute('''
        SELECT s.id, u.name, s.student_id, s.course,
               ROUND(AVG((g.mid_term+g.final+g.assignment)/3), 1) as avg_grade
        FROM students s
        JOIN users u ON s.user_id=u.id
        LEFT JOIN grades g ON g.student_id=s.id
        GROUP BY s.id
    ''').fetchall()
    
    db.close()
    
    standing_groups = {
        "Dean's List (Excellent)": [],
        "Honor Roll": [],
        "Good Standing": [],
        "Regular Standing": [],
        "Academic Probation": [],
        "At-Risk": []
    }
    
    for student in students:
        grade_val = student['avg_grade'] or 0
        gpa = calculate_gpa([grade_val])
        standing = get_academic_standing(gpa)
        standing_groups[standing].append({
            'name': student['name'],
            'student_id': student['student_id'],
            'course': student['course'],
            'gpa': gpa,
            'average_grade': grade_val
        })
    
    return jsonify(standing_groups)

# ─── STATIC / FRONTEND ────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)
    init_db()
    print("EduCore started at http://localhost:5000")
    app.run(debug=True, port=5000)
