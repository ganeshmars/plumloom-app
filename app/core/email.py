from typing import List, Optional
import os

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition, ContentId
import base64

from app.core.config import get_settings
from app.core.logging_config import logger

settings = get_settings()

async def send_email(
    to_email: str,
    subject: str,
    content: str,
    from_email: Optional[str] = None,
    attachments: Optional[List[dict]] = None
) -> bool:
    """
    Send an email using SendGrid API.
    
    Args:
        to_email: Recipient email address
        subject: Email subject
        content: HTML content of the email
        from_email: Sender email address (defaults to configured email)
        attachments: List of attachment dicts with keys: content, filename, type, disposition
        
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Use configured sender email if not provided
        sender = from_email or settings.EMAIL_SENDER
        
        # Create the email message
        message = Mail(
            from_email=sender,
            to_emails=to_email,
            subject=subject,
            html_content=content
        )
        
        # Add attachments if provided
        if attachments:
            for attachment_data in attachments:
                attachment = Attachment()
                attachment.file_content = FileContent(attachment_data["content"])
                attachment.file_name = FileName(attachment_data["filename"])
                attachment.file_type = FileType(attachment_data["type"])
                attachment.disposition = Disposition(attachment_data.get("disposition", "attachment"))
                if "content_id" in attachment_data:
                    attachment.content_id = ContentId(attachment_data["content_id"])
                message.add_attachment(attachment)
        
        # Initialize SendGrid client with API key
        sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
        
        # Send the email
        response = sg.send(message)
        
        # Log the response
        logger.info(f"Email sent to {to_email}, status code: {response.status_code}")
        
        # Return success if status code is 2xx
        return 200 <= response.status_code < 300
        
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}")
        return False


