"""
Email templates for various notifications sent through the application.
"""

def payment_confirmation_template(product_name, amount_str, date, transaction_id):
    """
    Template for payment confirmation emails.
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #f8f9fa; padding: 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .content {{ padding: 20px; }}
            .footer {{ background-color: #f8f9fa; padding: 15px; text-align: center; font-size: 12px; color: #666; border-radius: 0 0 5px 5px; }}
            .highlight {{ background-color: #f0f7ff; padding: 15px; border-left: 4px solid #0066cc; margin: 20px 0; }}
            h1 {{ color: #0066cc; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Payment Confirmation</h1>
            </div>
            <div class="content">
                <p>Hello,</p>
                <p>Thank you for your purchase! Your payment has been successfully processed.</p>
                
                <div class="highlight">
                    <h3>Payment Details:</h3>
                    <p><strong>Product:</strong> {product_name}</p>
                    <p><strong>Amount:</strong> {amount_str}</p>
                    <p><strong>Date:</strong> {date}</p>
                    <p><strong>Transaction ID:</strong> {transaction_id}</p>
                </div>
                
                <p>If you have any questions about your purchase or need assistance, please don't hesitate to contact our support team.</p>
                
                <p>Thank you for your business!</p>
            </div>
            <div class="footer">
                <p>This is an automated message, please do not reply directly to this email.</p>
            </div>
        </div>
    </body>
    </html>
    """

def subscription_welcome_template(product_name, price_info, status, current_period_end, trial_info=""):
    """
    Template for subscription welcome emails.
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #f8f9fa; padding: 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .content {{ padding: 20px; }}
            .footer {{ background-color: #f8f9fa; padding: 15px; text-align: center; font-size: 12px; color: #666; border-radius: 0 0 5px 5px; }}
            .highlight {{ background-color: #f0f7ff; padding: 15px; border-left: 4px solid #0066cc; margin: 20px 0; }}
            h1 {{ color: #0066cc; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Welcome to {product_name}!</h1>
            </div>
            <div class="content">
                <p>Hello,</p>
                <p>Thank you for subscribing to <strong>{product_name}</strong>! Your subscription has been successfully activated.</p>
                
                <div class="highlight">
                    <h3>Subscription Details:</h3>
                    {price_info}
                    <p><strong>Status:</strong> {status}</p>
                    <p><strong>Current period ends:</strong> {current_period_end}</p>
                    {trial_info}
                </div>
                
                <p>If you have any questions about your subscription or need assistance, please don't hesitate to contact our support team.</p>
                
                <p>We're excited to have you on board!</p>
            </div>
            <div class="footer">
                <p>This is an automated message, please do not reply directly to this email.</p>
            </div>
        </div>
    </body>
    </html>
    """

def subscription_updated_template(product_name, price_info, status, current_period_end):
    """
    Template for subscription update emails.
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #f8f9fa; padding: 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .content {{ padding: 20px; }}
            .footer {{ background-color: #f8f9fa; padding: 15px; text-align: center; font-size: 12px; color: #666; border-radius: 0 0 5px 5px; }}
            .highlight {{ background-color: #f0f7ff; padding: 15px; border-left: 4px solid #0066cc; margin: 20px 0; }}
            h1 {{ color: #0066cc; }}
            .button {{ display: inline-block; background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-top: 15px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Subscription Updated</h1>
            </div>
            <div class="content">
                <p>Hello,</p>
                <p>Your subscription to <strong>{product_name}</strong> has been successfully updated.</p>
                
                <div class="highlight">
                    <h3>Subscription Details:</h3>
                    {price_info}
                    <p><strong>Status:</strong> {status}</p>
                    <p><strong>Current period ends:</strong> {current_period_end}</p>
                </div>
                
                <p>You can manage your subscription at any time through your account dashboard.</p>
                
                <p>If you have any questions about your subscription or need assistance, please don't hesitate to contact our support team.</p>
                
                <p>Thank you for your continued business!</p>
            </div>
            <div class="footer">
                <p>This is an automated message, please do not reply directly to this email.</p>
            </div>
        </div>
    </body>
    </html>
    """

def subscription_cancelled_template(product_name, plan_details_html, subscription_duration, cancellation_reason, cancelled_at):
    """
    Template for subscription cancellation emails.
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #f8f9fa; padding: 20px; text-align: center; border-radius: 5px 5px 0 0; }}
            .content {{ padding: 20px; }}
            .footer {{ background-color: #f8f9fa; padding: 15px; text-align: center; font-size: 12px; color: #666; border-radius: 0 0 5px 5px; }}
            .highlight {{ background-color: #fff0f0; padding: 15px; border-left: 4px solid #dc3545; margin: 20px 0; }}
            h1 {{ color: #dc3545; }}
            .button {{ display: inline-block; background-color: #0066cc; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin-top: 15px; text-align: center; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Subscription Cancelled</h1>
            </div>
            <div class="content">
                <p>Hello,</p>
                <p>Your subscription to <strong>{product_name}</strong> has been cancelled.</p>
                
                <div class="highlight">
                    <h3>Subscription Details:</h3>
                    {plan_details_html}
                    {subscription_duration}
                    <p><strong>Cancellation reason:</strong> {cancellation_reason}</p>
                    <p><strong>Cancelled on:</strong> {cancelled_at}</p>
                </div>
                
                <p>If you did not request this cancellation or if you have any questions, please contact our support team immediately.</p>
                
                <p>We're sorry to see you go and hope you'll consider rejoining us in the future.</p>
            </div>
            <div class="footer">
                <p>This is an automated message, please do not reply directly to this email.</p>
            </div>
        </div>
    </body>
    </html>
    """

def invoice_payment_success_template(product_name, amount_str, payment_date, invoice_number, invoice_url):
    """
    Template for successful invoice payment notification.
    """
    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 5px;">
        <h2 style="color: #333; border-bottom: 1px solid #eee; padding-bottom: 10px;">Payment Confirmation</h2>
        
        <p>Hello,</p>
        
        <p>Thank you for your payment for {product_name}. Your payment has been successfully processed.</p>
        
        <div style="background-color: #f9f9f9; padding: 15px; border-radius: 4px; margin: 15px 0;">
            <p><strong>Payment Details:</strong></p>
            <p>Amount: {amount_str}</p>
            <p>Date: {payment_date}</p>
            <p>Invoice Number: {invoice_number}</p>
            <p>Status: Paid</p>
        </div>
        
        <p>You can view your invoice by clicking the button below:</p>
        
        <div style="text-align: center; margin: 25px 0;">
            <a href="{invoice_url}" style="background-color: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold;">View Invoice</a>
        </div>
        
        <p>If you have any questions about this payment, please contact our support team.</p>
        
        <p>Thank you for your business!</p>
        
        <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #eee; font-size: 12px; color: #777;">
            <p>This is an automated message, please do not reply directly to this email.</p>
        </div>
    </div>
    """

def invoice_created_template(product_name, amount_str, due_date_str, status, hosted_invoice_url):
    """
    Template for new invoice notification.
    """
    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 5px;">
        <h2 style="color: #333; border-bottom: 1px solid #eee; padding-bottom: 10px;">New Invoice Available</h2>
        
        <p>Hello,</p>
        
        <p>A new invoice has been created for your {product_name} subscription.</p>
        
        <div style="background-color: #f9f9f9; padding: 15px; border-radius: 4px; margin: 15px 0;">
            <p><strong>Invoice Details:</strong></p>
            <p>Amount Due: {amount_str}</p>
            <p>Due Date: {due_date_str}</p>
            <p>Status: {status.capitalize()}</p>
        </div>
        
        <p>You can view and pay your invoice by clicking the button below:</p>
        
        <div style="text-align: center; margin: 25px 0;">
            <a href="{hosted_invoice_url}" style="background-color: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold;">View Invoice</a>
        </div>
        
        <p>If you have any questions about this invoice, please contact our support team.</p>
        
        <p>Thank you for your business!</p>
        
        <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #eee; font-size: 12px; color: #777;">
            <p>This is an automated message, please do not reply directly to this email.</p>
        </div>
    </div>
    """ 