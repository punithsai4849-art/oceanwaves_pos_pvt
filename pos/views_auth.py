import random
import string
from django.shortcuts import render, redirect
from django.contrib import messages
from django.core.mail import send_mail
from django.core.cache import cache
from django.contrib.auth.models import User
from django_ratelimit.decorators import ratelimit
from django.conf import settings

@ratelimit(key='ip', rate='5/m', block=True)
def forgot_password_view(request):
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        user = User.objects.filter(username=username).first()
        if user and user.email:
            email = user.email
            otp = ''.join(random.choices(string.digits, k=6))
            cache.set(f"pwd_reset_otp_{email}", otp, timeout=300) # 5 minutes
            
            subject = "OCEANWAVES POS - Password Reset OTP"
            message = f"Hello {user.username},\n\nYour OTP to reset your password is: {otp}\nThis code will expire in 5 minutes.\n\nIf you did not request this, please ignore this email.\n\nOCEANWAVES SEA FOODS is part of OCEANWAVES VICTUALS PRIVATE LIMITED.\n— OCEANWAVES POS System"
            try:
                send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
                request.session['reset_email'] = email
                
                # Show a masked email hint
                parts = email.split('@')
                hint = f"{parts[0][:2]}***@{parts[1]}" if len(parts) == 2 else email
                messages.success(request, f"An OTP has been sent to the email associated with your account ({hint}).")
                return redirect('verify_otp')
            except Exception as e:
                import logging
                logging.getLogger('django').error(f"Failed to send OTP email: {e}")
                messages.error(request, "Failed to send email. Please check terminal/server configuration.")
                return redirect('forgot_password')
        else:
            messages.error(request, "User not found or no email is associated with this username.")
            return redirect('forgot_password')
            
    return render(request, 'pos/forgot_password.html')


@ratelimit(key='ip', rate='10/m', block=True)
def verify_otp_view(request):
    email = request.session.get('reset_email', '')
    
    if request.method == 'POST':
        typed_email = request.POST.get('email', '').strip()
        email_to_check = email or typed_email
        
        otp = request.POST.get('otp', '').strip()
        
        if not email_to_check:
            messages.error(request, "Session expired, please restart password reset.")
            return redirect('forgot_password')
            
        cached_otp = cache.get(f"pwd_reset_otp_{email_to_check}")
        
        if cached_otp and cached_otp == otp:
            request.session['otp_verified_email'] = email_to_check
            return redirect('reset_password_final')
        else:
            messages.error(request, "Invalid or expired OTP.")
            
    return render(request, 'pos/verify_otp.html', {'email': email})

def reset_password_final_view(request):
    email = request.session.get('otp_verified_email')
    if not email:
        messages.error(request, "Unauthorized access.")
        return redirect('forgot_password')
        
    if request.method == 'POST':
        pwd1 = request.POST.get('password')
        pwd2 = request.POST.get('confirm_password')
        
        if pwd1 != pwd2:
            messages.error(request, "Passwords do not match.")
        else:
            from django.contrib.auth.password_validation import validate_password
            from django.core.exceptions import ValidationError
            try:
                validate_password(pwd1)
                user = User.objects.filter(email=email).first()
                if user:
                    user.set_password(pwd1)
                    user.save()
                    messages.success(request, "Password has been successfully reset. You can now log in.")
                
                # Clean up
                cache.delete(f"pwd_reset_otp_{email}")
                if 'otp_verified_email' in request.session:
                    del request.session['otp_verified_email']
                if 'reset_email' in request.session:
                    del request.session['reset_email']
                    
                return redirect('login')
                
            except ValidationError as e:
                for error in e.messages:
                    messages.error(request, error)
                
    return render(request, 'pos/reset_password.html')
