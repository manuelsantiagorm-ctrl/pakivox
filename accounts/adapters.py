# accounts/adapters.py
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class CompanyRegistrationAccountAdapter(DefaultAccountAdapter):
    """
    Cierra el alta local de allauth (/accounts/signup/).

    Las empresas se registran por accounts:company_register, que verifica el
    correo antes de crear la empresa, el usuario y el periodo de prueba. El
    alta local de allauth creaba usuarios sin verificar que despues podian
    crear una empresa desde el onboarding, saltandose esa verificacion.
    """

    def is_open_for_signup(self, request):
        return False


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        # Google ya verifica el correo; el alta con Google sigue abierta aunque
        # el alta local este cerrada (por defecto allauth heredaria el cierre).
        return True

    def get_login_redirect_url(self, request):
        user = request.user

        # Si el usuario NO tiene empresa asignada aún, lo mandamos a completar los datos
        profile = getattr(user, "profile", None)
        if profile is None or not getattr(profile, "company_id", None):
            return "/accounts/onboarding-empresa/"

        # Si ya tiene empresa, va al dashboard
        return "/"
