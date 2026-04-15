from app import db


class FormResponse(db.Model):
    __tablename__ = "form_responses"

    id = db.Column(db.Integer, primary_key=True)
    estudiante_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    evaluacion_id = db.Column(db.Integer, db.ForeignKey("evaluaciones.id"), nullable=True, index=True)
    submission_id = db.Column(db.String(120), nullable=True, index=True)
    respondent_id = db.Column(db.String(120), nullable=True)
    form_id = db.Column(db.String(40), nullable=True)
    matricula_estudiante = db.Column(db.String(24), nullable=True, index=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    payload_json = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(30), nullable=False, default="tally")
    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp(), nullable=False)
    fecha_actualizacion = db.Column(
        db.DateTime,
        default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp(),
        nullable=False,
    )

    estudiante = db.relationship("User", foreign_keys=[estudiante_id], backref="form_responses")
    evaluacion = db.relationship("Evaluacion", foreign_keys=[evaluacion_id], backref="form_response_rows")

    def __repr__(self):
        return f"<FormResponse id={self.id} estudiante={self.estudiante_id} submission={self.submission_id}>"
