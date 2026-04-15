from app import db


class ProfileEditRequest(db.Model):
    __tablename__ = "profile_edit_requests"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="pendiente", index=True)
    request_payload_json = db.Column(db.Text, nullable=False)
    admin_note = db.Column(db.Text, nullable=True)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    requested_at = db.Column(db.DateTime, default=db.func.current_timestamp(), nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)

    requester = db.relationship(
        "User",
        foreign_keys=[user_id],
        backref=db.backref("profile_edit_requests", lazy="dynamic", cascade="all, delete-orphan"),
    )
    reviewer = db.relationship("User", foreign_keys=[reviewed_by], backref="reviewed_profile_requests")

    def __repr__(self):
        return f"<ProfileEditRequest id={self.id} user={self.user_id} status={self.status}>"
