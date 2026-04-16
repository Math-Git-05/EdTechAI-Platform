from app import db


class Evaluacion(db.Model):
    __tablename__ = "evaluaciones"

    id = db.Column(db.Integer, primary_key=True)
    estudiante_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    estado = db.Column(db.String(30), nullable=False, default="completada")
    origen = db.Column(db.String(40), nullable=False, default="tally")
    results_released = db.Column(db.Boolean, nullable=False, default=False)
    referencia_externa = db.Column(db.String(120), nullable=True)
    form_id = db.Column(db.String(40), nullable=True)
    respondent_id = db.Column(db.String(120), nullable=True)
    matricula_estudiante = db.Column(db.String(24), nullable=True, index=True)
    submitted_at = db.Column(db.DateTime, nullable=True)

    logical_reasoning_score = db.Column(db.Float, nullable=True)
    problem_resolution_score = db.Column(db.Float, nullable=True)
    detail_attention_score = db.Column(db.Float, nullable=True)
    creativity_score = db.Column(db.Float, nullable=True)
    tech_ability_score = db.Column(db.Float, nullable=True)
    average_score = db.Column(db.Float, nullable=True)

    datos_json = db.Column(db.Text, nullable=True)
    comentario_profesor = db.Column(db.Text, nullable=True)
    comentario_profesor_at = db.Column(db.DateTime, nullable=True)
    profesor_comentario_id = db.Column(db.Integer, nullable=True)
    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp(), nullable=False)
    fecha_actualizacion = db.Column(
        db.DateTime,
        default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp(),
        nullable=False,
    )

    def __repr__(self):
        return f"<Evaluacion {self.id} estudiante={self.estudiante_id} estado={self.estado}>"
