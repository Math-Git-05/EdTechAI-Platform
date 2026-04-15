from app import db


class Calificacion(db.Model):
    __tablename__ = "calificaciones"

    id = db.Column(db.Integer, primary_key=True)
    estudiante_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    asignatura = db.Column(db.String(120), nullable=False)
    valor = db.Column(db.Float, nullable=False)
    periodo = db.Column(db.String(40), nullable=True)
    anio = db.Column(db.Integer, nullable=True)
    observaciones = db.Column(db.Text, nullable=True)
    fecha_creacion = db.Column(db.DateTime, default=db.func.current_timestamp(), nullable=False)
    fecha_actualizacion = db.Column(
        db.DateTime,
        default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp(),
        nullable=False,
    )

    def __repr__(self):
        return f"<Calificacion {self.id} estudiante={self.estudiante_id} valor={self.valor}>"
