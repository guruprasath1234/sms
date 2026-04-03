import sqlite3, json, hashlib, os, csv, io, uuid
from werkzeug.utils import secure_filename
from flask import Flask, request, jsonify, session, send_from_directory, make_response
from datetime import datetime, timedelta
import random

app = Flask(__name__, static_folder='static')
app.secret_key = 'educore_secret_2024'
DB = 'educore.db'
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
            role TEXT NOT NULL CHECK(role IN ('admin','teacher','student')),
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
    ''')
    # Migrate existing database to new schema fields where needed
    cols = [r['name'] for r in db.execute('PRAGMA table_info(materials)').fetchall()]
    if 'file_path' not in cols:
        db.execute('ALTER TABLE materials ADD COLUMN file_path TEXT')

    cols = [r['name'] for r in db.execute('PRAGMA table_info(assignments)').fetchall()]
    if 'url' not in cols:
        db.execute('ALTER TABLE assignments ADD COLUMN url TEXT')

    db.commit()
    _seed(db)
    db.close()

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

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

    db.commit()

# ─── AUTH ──────────────────────────────────────────────────────────────────────
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
    d = request.json
    db = get_db()
    u = db.execute('SELECT * FROM users WHERE email=? AND password=?',
                   (d['email'], hash_pw(d['password']))).fetchone()
    db.close()
    if not u:
        return jsonify({'error':'Invalid credentials'}), 401
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
    rows = db.execute('''
        SELECT s.id, s.student_id, u.name, s.course, s.status, s.year,
               u.email, u.phone,
               ROUND(AVG((g.mid_term+g.final+g.assignment)/3),1) as avg_grade,
               ROUND(100.0*SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END)/MAX(1,COUNT(a.id)),1) as attendance
        FROM students s
        JOIN users u ON s.user_id=u.id
        LEFT JOIN grades g ON g.student_id=s.id
        LEFT JOIN attendance a ON a.student_id=s.id
        GROUP BY s.id
        ORDER BY u.name
    ''').fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

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
    rows = db.execute('''
        SELECT g.id, u.name as student_name, s.student_id, g.subject,
               g.mid_term, g.final, g.assignment,
               ROUND((g.mid_term+g.final+g.assignment)/3,1) as total
        FROM grades g
        JOIN students s ON g.student_id=s.id
        JOIN users u ON s.user_id=u.id
        ORDER BY u.name
    ''').fetchall()
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

# ─── STATIC / FRONTEND ────────────────────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('static', 'index.html')

if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)
    init_db()
    print("✅ EduCore started at http://localhost:5000")
    app.run(debug=True, port=5000)
