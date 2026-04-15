# EdTech Platform - Frontend Implementation

## 📋 Project Overview

EdTech is a **professional technical career guidance platform** built with Flask. Students take a 50-question aptitude evaluation via Tally form, and an AI system provides personalized recommendations for one of four technical fields:

- **Informática** 🖥️ (Computer Science)
- **Enfermería** ⚕️ (Nursing)
- **Administración** 📊 (Business Administration)
- **Comercio** 💼 (Commerce & Marketing)

## 🏗️ Architecture

### Technology Stack
- **Backend**: Flask 3.0.3 + Flask-SQLAlchemy 3.1.1
- **Database**: SQLite (file-based, no server needed)
- **Authentication**: Flask-Login 0.6.3 + Werkzeug security
- **Forms**: Flask-WTF 1.2.2 (CSRF protection)
- **Frontend**: Jinja2 templates + Modern CSS3
- **External**: Tally form integration for aptitude evaluation

### Project Structure
```
app/
├── controllers/          # Blueprint routes
│   ├── main_controller.py
│   ├── auth_controller.py        
│   ├── dashboard_controller.py
│   ├── estudiante_controller.py
│   ├── profesor_controller.py
│   ├── admin_controller.py
│   ├── formulario_controller.py
│   └── resultado_controller.py
├── models/              # SQLAlchemy ORM
│   └── user.py          # User model with roles
├── templates/           # Jinja2 templates
│   ├── layout/base.html # Master template
│   ├── auth/            # Login/Register
│   ├── dashboard/       # Role-based dashboards
│   ├── formulario.html  # Tally embed
│   └── resultado.html   # Results display
├── static/
│   └── css/
│       ├── main.css            # Global styles & theme
│       ├── auth.css            # Auth pages styling
│       ├── dashboard.css       # Dashboard layouts
│       ├── form.css            # Form page styling
│       └── result.css          # Results page styling
├── config.py            # Environment configuration
└── __init__.py          # Application factory

requirements.txt        # Dependencies
instance/edtech.db     # SQLite database
```

## 🎨 User Roles & Dashboards

### 1. **Estudiante** (Student)
- Completes 50-question aptitude evaluation
- Views personalized results with affinity %
- Sees recommended technical field
- Can retake evaluation anytime

### 2. **Profesor** (Teacher)
- Views list of assigned students
- Tracks evaluation completion status
- Can view student results

### 3. **Admin**
- System statistics (total users, students, teachers)
- User management
- Platform overview

## 🔐 Authentication

### Login Flow
1. User enters email + password
2. Credentials validated against database
3. Password verified with `check_password_hash()`
4. `login_user()` creates session via Flask-Login
5. User redirected to role-specific dashboard

### Registration Flow
1. User provides: nombre, apellido, email, rol, password
2. Email checked for duplicates
3. Password hashed with `generate_password_hash()`
4. New User created with default role (estudiante)
5. Redirects to login page

## 🎯 Key Features Implemented

### ✅ Completed
- [x] Professional authentication system with password hashing
- [x] Role-based access control (3 roles)
- [x] SQLite database with User model
- [x] Responsive login/registration forms
- [x] Three separate dashboards (one per role)
- [x] Dark/Light theme toggle with localStorage
- [x] Beautiful CSS with hover effects & transitions
- [x] Tally form integration (embedded iframe)
- [x] Results display page with affinity charts
- [x] Mobile-responsive design
- [x] All routes properly registered with Flask

### 🛠️ In Progress / Future
- [ ] Tally webhook handler (receive form submissions)
- [ ] Resultado model & database storage
- [ ] AI recommendation algorithm
- [ ] Student evaluation history
- [ ] PDF export functionality
- [ ] Chat/AI assistant interface
- [ ] Email notifications
- [ ] Password recovery
- [ ] Admin user management interface

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Flask 3.0.3
- SQLite3 (included with Python)

### Installation

```bash
# 1. Navigate to project
cd edtech-platform

# 2. Create virtual environment
python -m venv venv
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run application
python -m flask run
```

The app will be available at **http://localhost:5000**

### Default Admin Account
After first run, the database is created. You can register as a new user, but to access admin features, modify the `role` field in the database:

```python
# In Python shell:
from app import create_app, db
from app.models.user import User

app = create_app()
with app.app_context():
    admin = User.query.filter_by(email='admin@edtech.com').first()
    if admin:
        admin.role = 'admin'
        db.session.commit()
```

## 📱 Pages & Routes

| Path | Role | Page |
|------|------|------|
| `/` | Public | Home |
| `/landing` | Public | Landing page |
| `/auth/login` | Public | Login |
| `/auth/register` | Public | Register |
| `/dashboard/` | Logged-in | Role redirect |
| `/dashboard/estudiante/` | Estudiante | Student dashboard |
| `/dashboard/profesor/` | Profesor | Teacher dashboard |
| `/dashboard/admin/` | Admin | Admin panel |
| `/formulario/` | Estudiante | Tally evaluation form |
| `/resultado/` | Estudiante | Results page |

## 🎨 Design System

### Colors by Technical Field
- **INF** (Informática): Blue #2563eb
- **ENF** (Enfermería): Green #059669
- **ADM** (Administración): Purple #7c3aed
- **COM** (Comercio): Orange #d97706

### Theme System
- **Dark Theme** (Default): bg=#0B1426, accent=#2563eb
- **Light Theme**: bg=#f0f4ff, accent=#2563eb
- Toggle stored in localStorage
- CSS variables automatically switch

### Typography
- Headers: Semibold (600) / Bold (700)
- Body: Regular (400)
- Small text: 12-13px
- Large headings: 24-28px

## 🔗 Database Schema

### User Table
```sql
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  nombre VARCHAR(100) NOT NULL,
  apellido VARCHAR(100) NOT NULL,
  email VARCHAR(120) UNIQUE NOT NULL,
  password VARCHAR(255) NOT NULL,
  role VARCHAR(20) DEFAULT 'estudiante',
  fecha_creacion DATETIME DEFAULT NOW()
)
```

Roles: `"estudiante"`, `"profesor"`, `"admin"`

## 📊 Frontend Components

### Reusable Classes

**Buttons**
```html
<a class="btn btn-primary">Primary Action</a>
<a class="btn btn-secondary">Secondary Action</a>
<a class="btn-small">Small Button</a>
```

**Cards**
```html
<div class="dashboard-card">
  <div class="card-header inf">📝</div>
  <h3>Card Title</h3>
  <p>Description text</p>
  <a class="btn-small">Action</a>
</div>
```

**Badges**
```html
<span class="badge-primary">Admin</span>
<span class="badge-secondary">Pending</span>
```

**Tables**
```html
<table>
  <thead><tr><th>Header</th></tr></thead>
  <tbody><tr><td>Data</td></tr></tbody>
</table>
```

## 🧪 Testing

Run the test script to verify setup:
```bash
python test_app.py
```

Expected output:
```
✓ Database connected successfully!
✓ Total users: 1
✓ Routes registered: [15 routes]
✓ Application is ready!
```

## 📝 Configuration

Edit `app/config.py` to adjust:
- Database path
- Debug mode
- Secret key
- Session timeout

## 🐛 Troubleshooting

### "ModuleNotFoundError: No module named 'flask'"
```bash
pip install -r requirements.txt
```

### "Error: 404 Not Found" on login
Ensure you're using `/auth/login` not `/login`

### Database file not created
Check that `instance/` directory exists. Flask-SQLAlchemy creates it automatically on first run.

### Theme not switching
Check browser LocalStorage is enabled. Theme toggle uses `localStorage` to persist preference.

## 📄 License

This project is for educational purposes.

## 👨‍💻 Author

Developed as part of the EdTech platform initiative.

---

**Last Updated**: January 2024
**Status**: Frontend Complete ✅ | Backend Infrastructure Ready 🚀
