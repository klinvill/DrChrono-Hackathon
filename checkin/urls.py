from django.conf.urls import url
from django.views import generic
import views


app_name = 'checkin'

urlpatterns = [
    url(r'^$', views.doctor_login),
    url(r'^login/$', views.doctor_login, name="doctor-login"),
    url(r'^oauth2/$', views.auth),
    url(r'^dashboard/$', views.dashboard, name="dashboard"),
    url(r'^checkin/$', generic.TemplateView.as_view(template_name="checkin/patient_checkin.html"), name="checkin-view"),
    url(r'^handle_checkin/$', views.handle_checkin, name="handle-checkin"),
    url(r'^patient/update/$', views.update_patient, name="update-patient"),
    url(r'^patient/(?P<patient_id>[0-9]+)/$', views.get_patient, name="get-patient"),
    url(r'^appointment/(?P<appointment_id>[0-9]+)/start/$', views.start_appointment, name="start-appointment"),
]