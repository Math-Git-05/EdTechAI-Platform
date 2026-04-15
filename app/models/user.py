from app import db
from flask_login import UserMixin


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    apellido = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default="estudiante", nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp())

    # Nuevos campos para flujo de autenticacion completo
    email_verificado = db.Column(db.Boolean, nullable=False, default=False)
    email_verificado_at = db.Column(db.DateTime, nullable=True)
    ultimo_login = db.Column(db.DateTime, nullable=True)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    reset_requested_at = db.Column(db.DateTime, nullable=True)
    profesor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    seccion = db.Column(db.String(50), nullable=True)

    profesor = db.relationship(
        "User",
        remote_side=[id],
        foreign_keys=[profesor_id],
        backref=db.backref("estudiantes_asignados", lazy="dynamic"),
    )
    evaluaciones = db.relationship(
        "Evaluacion",
        backref="estudiante",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    calificaciones = db.relationship(
        "Calificacion",
        backref="estudiante_calificado",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    student_profile = db.relationship(
        "StudentProfile",
        backref="student_user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    teacher_profile = db.relationship(
        "TeacherProfile",
        backref="teacher_user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    @property
    def is_active(self):
        return self.activo

    def __repr__(self):
        return f"<User {self.nombre} {self.apellido} ({self.role})>"
