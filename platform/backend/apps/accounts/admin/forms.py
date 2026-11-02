from django import forms
from django.contrib.auth.forms import UserChangeForm
from django.contrib.auth.password_validation import validate_password
from apps.accounts.admin.models import Administrator

class AdministratorCreationForm(forms.ModelForm):
    password1 = forms.CharField(widget=forms.PasswordInput, label='Mot de passe')
    password2 = forms.CharField(widget=forms.PasswordInput, label='Confirmer le mot de passe')

    class Meta:
        model = Administrator
        fields = ('email', 'first_name', 'last_name')

    def clean_password2(self):
        p1 = self.cleaned_data.get('password1')
        p2 = self.cleaned_data.get('password2')
        if p1 != p2:
            raise forms.ValidationError('Les mots de passe ne correspondent pas.')
        validate_password(p2)
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user

class AdministratorChangeForm(UserChangeForm):
    class Meta:
        model = Administrator
        fields = '__all__'
