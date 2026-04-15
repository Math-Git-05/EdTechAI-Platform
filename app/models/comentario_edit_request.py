from app import db


class ComentarioEditRequest(db.Model):
    __tablename__ = "comentario_edit_requests"

    id = db.Column(db.Integer, primary_key=True)
    profesor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    estudiante_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="pendiente", index=True)
    teacher_note = db.Column(db.Text, nullable=True)
    admin_note = db.Column(db.Text, nullable=True)
    requested_at = db.Column(db.DateTime, default=db.func.current_timestamp(), nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    used_at = db.Column(db.DateTime, nullable=True)

    profesor = db.relationship("User", foreign_keys=[profesor_id], backref="comentario_edit_requests")
    estudiante = db.relationship("User", foreign_keys=[estudiante_id], backref="comentario_edit_student_requests")
    reviewer = db.relationship("User", foreign_keys=[reviewed_by], backref="comentario_edit_reviewed_requests")

    def __repr__(self):
        return f"<ComentarioEditRequest id={self.id} profesor={self.profesor_id} estudiante={self.estudiante_id} status={self.status}>"
