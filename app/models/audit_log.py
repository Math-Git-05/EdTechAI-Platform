from app import db


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    action = db.Column(db.String(80), nullable=False, index=True)
    target_type = db.Column(db.String(80), nullable=True, index=True)
    target_id = db.Column(db.String(120), nullable=True, index=True)
    metadata_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp(), nullable=False, index=True)

    actor = db.relationship("User", foreign_keys=[actor_user_id], backref="audit_events")

    def __repr__(self):
        return f"<AuditLog id={self.id} action={self.action} actor={self.actor_user_id}>"
