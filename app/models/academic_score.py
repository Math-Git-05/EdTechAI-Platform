from app import db


class AcademicScore(db.Model):
    __tablename__ = "academic_scores"

    id = db.Column(db.Integer, primary_key=True)
    estudiante_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    student_id = db.Column(db.String(24), nullable=True, index=True)
    anio = db.Column(db.Integer, nullable=True, index=True)

    math_average = db.Column(db.Float, nullable=True)
    language_average = db.Column(db.Float, nullable=True)
    science_average = db.Column(db.Float, nullable=True)
    overall_average = db.Column(db.Float, nullable=True)
    period_count = db.Column(db.Integer, nullable=True)

    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp(), nullable=False)
    fecha_actualizacion = db.Column(
        db.DateTime,
        default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp(),
        nullable=False,
    )

    estudiante = db.relationship("User", foreign_keys=[estudiante_id], backref="academic_scores")

    def __repr__(self):
        return (
            f"<AcademicScore id={self.id} estudiante={self.estudiante_id} anio={self.anio} "
            f"overall={self.overall_average}>"
        )
