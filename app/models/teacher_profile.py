from app import db


class TeacherProfile(db.Model):
    __tablename__ = "teacher_profiles"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True, index=True)

    employee_id = db.Column(db.String(30), nullable=True, unique=True)
    especialidad = db.Column(db.String(120), nullable=True)
    departamento = db.Column(db.String(120), nullable=True)
    telefono = db.Column(db.String(30), nullable=True)
    school_id = db.Column(db.String(30), nullable=False, default="10-03-00092")
    labor_status = db.Column(db.String(30), nullable=False, default="activo")

    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp(), nullable=False)
    fecha_actualizacion = db.Column(
        db.DateTime,
        default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp(),
        nullable=False,
    )

    def __repr__(self):
        return f"<TeacherProfile user={self.user_id} employee_id={self.employee_id}>"
