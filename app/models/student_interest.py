from app import db


class StudentInterest(db.Model):
    __tablename__ = "student_interests"

    student_id = db.Column(db.String(24), primary_key=True)
    interest_technology = db.Column(db.Float, nullable=True, default=0)
    interest_design = db.Column(db.Float, nullable=True, default=0)
    interest_business = db.Column(db.Float, nullable=True, default=0)
    interest_health = db.Column(db.Float, nullable=True, default=0)

    def __repr__(self):
        return f"<StudentInterest student_id={self.student_id}>"
