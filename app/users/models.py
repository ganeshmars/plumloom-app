# from datetime import datetime
# from sqlalchemy import Column, String, DateTime, Boolean, Index, ForeignKey, Table, text
# from sqlalchemy.dialects.postgresql import JSONB, ARRAY
# from sqlalchemy.orm import relationship, backref

# from app.models.base import Base


# class User(Base):
#     """
#     User model representing application users with authentication and profile details
#     """
#     __tablename__ = "users"

#     # Primary identifier
#     id = Column(String, primary_key=True)
    
#     # Stores multiple login IDs as an array ['email1@example.com', 'email2@example.com']
#     descope_user_id = Column(String, nullable=True)
#     login_ids = Column(ARRAY(String),default=[])
#     is_email_verified = Column(Boolean, nullable=True)
#     is_phone_verified = Column(Boolean, nullable=True)
    
    
#     # User status
#     status = Column(String, default="inactive")  # active, inactive, suspended, deleted
#     is_active = Column(Boolean, default=True)
    
#     # User profile information
#     name = Column(String, nullable=True)
#     display_name = Column(String, nullable=True)
#     email = Column(String, nullable=False, unique=True)
#     phone = Column(String, nullable=True)
#     given_name = Column(String, nullable=True)
#     middle_name = Column(String, nullable=True)
#     family_name = Column(String, nullable=True)
    
#     # Access control
#     tenants = Column(ARRAY(String), default=[], nullable=True)
#     roles = Column(ARRAY(String), default=[], nullable=True)
    
#     # Metadata for future extensibility
#     user_metadata = Column(JSONB, nullable=True, default={})
    
   
#     # Timestamps
#     created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
#     logout_time = Column(DateTime, nullable=True)
#     consent_expiration = Column(DateTime, nullable=True)
    
#     # Flags
#     is_test_user = Column(Boolean, default=True)
    
#     # Additional details (can be extended as needed)
#     picture =  Column(String, nullable=True)
#     is_web_authnetication = Column(Boolean, default=False)
    
#     # Indexes for query performance
#     __table_args__ = (
#         Index('ix_users_email', 'email'),
#         Index('ix_users_descope_user_id', 'descope_user_id'),
#         Index('ix_users_phone', 'phone'),
#         Index('ix_users_status', 'status'),
#     )
    
#     def __repr__(self):
#         return f"<User(id={self.id},name={self.name}, email={self.email})>"
