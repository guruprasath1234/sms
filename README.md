# EduCore – Student Management System
## Python + SQLite Full-Stack Web Application

### Requirements
- Python 3.8+ (Flask is a standard library requirement)
- Flask: `pip install flask`

### Run
```bash
pip install flask
python app.py
```
Then open → **http://localhost:5000**

### Demo Credentials
| Role    | Email                       | Password   |
|---------|-----------------------------|------------|
| Admin   | admin@educore.com           | admin123   |
| Teacher | teacher1@educore.com        | teach123   |
| Student | student1@educore.com        | stud123    |

### Features by Role
| Feature           | Admin | Teacher | Student |
|-------------------|:-----:|:-------:|:-------:|
| Dashboard         | ✅    | ✅      | ✅      |
| View Students     | ✅    | ✅      | ✅      |
| Add/Edit Students | ✅    | ✅      | ❌      |
| Delete Students   | ✅    | ❌      | ❌      |
| Bulk Delete       | ✅    | ❌      | ❌      |
| Manage Teachers   | ✅    | ❌      | ❌      |
| Grades CRUD       | ✅    | ✅      | View    |
| Assignments CRUD  | ✅    | ✅      | View    |
| Materials CRUD    | ✅    | ✅      | View    |
| Attendance        | ✅    | ✅      | ❌      |
| Timetable         | ✅    | ✅      | View    |
| Export CSV        | ✅    | ✅      | ❌      |

### Project Structure
```
educore/
├── app.py           ← Flask backend + SQLite REST API
├── educore.db       ← Auto-created SQLite database (with 20 students seeded)
├── README.md
└── static/
    └── index.html   ← Complete SPA frontend
```

### Database Tables
- `users` – All users (admin, teacher, student)
- `students` – Student profile linked to users
- `teachers` – Teacher profile linked to users
- `grades` – Subject grades per student
- `assignments` – Assignments with status tracking
- `materials` – Study materials/resources
- `attendance` – Daily attendance records
- `announcements` – School announcements
- `timetable` – Class schedule grid

### API Endpoints
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /api/login | Login |
| POST | /api/logout | Logout |
| GET | /api/me | Current user |
| PUT | /api/profile | Update profile |
| GET | /api/dashboard | Dashboard stats |
| GET/POST | /api/students | List / Add students |
| PUT/DELETE | /api/students/:id | Update / Delete student |
| POST | /api/students/bulk-delete | Bulk delete |
| GET/POST | /api/teachers | List / Add teachers |
| DELETE | /api/teachers/:id | Delete teacher |
| GET/POST | /api/grades | List / Add grades |
| PUT/DELETE | /api/grades/:id | Update / Delete grade |
| GET/POST | /api/assignments | List / Add assignments |
| DELETE | /api/assignments/:id | Delete assignment |
| GET/POST | /api/materials | List / Add materials |
| DELETE | /api/materials/:id | Delete material |
| GET/POST | /api/attendance | List / Save attendance |
| GET/POST | /api/announcements | List / Add announcements |
| DELETE | /api/announcements/:id | Delete announcement |
| GET/POST | /api/timetable | Get / Add timetable entries |
| GET | /api/export/students/csv | Export students as CSV |
