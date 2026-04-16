from app import db


class GradeChangeRequest(db.Model):
    __tablename__ = "grade_change_requests"

    id = db.Column(db.Integer, primary_key=True)
    profesor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    estudiante_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    calificacion_id = db.Column(db.Integer, db.ForeignKey("calificaciones.id"), nullable=True, index=True)

    asignatura = db.Column(db.String(120), nullable=False)
    periodo = db.Column(db.String(40), nullable=True)
    anio = db.Column(db.Integer, nullable=True)
    valor = db.Column(db.Float, nullable=False)
    observaciones = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(20), nullable=False, default="pendiente", index=True)
    admin_note = db.Column(db.Text, nullable=True)
    requested_at = db.Column(db.DateTime, default=db.func.current_timestamp(), nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    profesor = db.relationship("User", foreign_keys=[profesor_id], backref="grade_change_requests")
    estudiante = db.relationship("User", foreign_keys=[estudiante_id], backref="grade_change_student_requests")
    reviewer = db.relationship("User", foreign_keys=[reviewed_by], backref="grade_change_reviewed_requests")
    calificacion = db.relationship("Calificacion", foreign_keys=[calificacion_id], backref="grade_change_requests")

    def __repr__(self):
        return (
            f"<GradeChangeRequest id={self.id} profesor={self.profesor_id} "
            f"estudiante={self.estudiante_id} status={self.status}>"
        )
