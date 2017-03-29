from django.shortcuts import render, redirect, reverse, get_object_or_404
from django.http import HttpResponseRedirect, Http404
from oauth2client.client import flow_from_clientsecrets, OAuth2Credentials
from oauth2client.contrib import xsrfutil
from django.conf import settings
from requests import HTTPError

from datetime import date, datetime
from math import floor
import requests

from models import TimeWaiting


FLOW = flow_from_clientsecrets(
    settings.GOOGLE_OAUTH2_CLIENT_SECRETS_JSON,
    scope=["patients:read", "patients:write", "calendar:read", "calendar:write", "clinical:read", "clinical:write"],
    redirect_uri="http://localhost:8000/oauth2"
)


def doctor_login(request):
    """
    Login view for a doctor to setup the check-in kiosk
    :param request:
    :return:
    """
    # TODO: OAUTH credentials are currently stored in a session rather than in the database. Handling needs to be added
    #       for refresh tokens
    access_token = request.session.get('credential')
    credential = OAuth2Credentials.new_from_json(json_data=access_token) if access_token else None

    if credential is None or credential.invalid is True:
        authorize_url = FLOW.step1_get_authorize_url()
        return HttpResponseRedirect(authorize_url)

    else:
        headers = {}
        credential.apply(headers)

        # TODO: implement more robust handling for when an access token expires, currently just deletes the stored
        #       token when a 401 message is encountered
        try:
            response = requests.get('https://drchrono.com/api/users/current', headers=headers)
            response.raise_for_status()

        except HTTPError as err:
            if err.response.status_code == 401:
                del request.session['credential']
                return redirect('checkin:doctor-login')
            else:
                raise

        data = response.json()

        # signed-in doctor's id is stored for use throughout the application
        #   (retrieving and modifying patients and appointments)
        request.session['doctor_id'] = data['id']

        return redirect("checkin:dashboard")


def dashboard(request):
    """
    Dashboard view for a doctor to see which patients have checked in along with the other patients they have later in
    the day. Also shows historic average time patient waiting time.
    :param request:
    :return:
    """
    checked_in_status = "Arrived"

    # TODO: could the credential process be turned into a simple decorator? Credential could then be automatically added
    #   to each function but would require extra documentation since it would not show up in the code
    access_token = request.session.get('credential')
    credential = OAuth2Credentials.new_from_json(json_data=access_token) if access_token else None

    # redirect to oauth2 login flow
    if credential is None:
        return redirect("checkin:doctor-login")

    doctor = request.session['doctor_id']

    appointments = get_todays_unhandled_appointments(credential, doctor)
    enriched_appointments = enrich_appointments(appointments, credential)

    checked_in_patients = []
    upcoming_appointments = []

    for appointment in enriched_appointments:
        if appointment['status'] == checked_in_status:
            checked_in_patients.append(appointment)
        else:
            upcoming_appointments.append(appointment)

    try:
        historic_wait_time = TimeWaiting.objects.get(doctor=doctor)
        historic_average_wait_time = historic_wait_time.average_waiting_time()

    except TimeWaiting.DoesNotExist:
        historic_average_wait_time = 0

    return render(request, "checkin/doctor_dashboard.html", {'doctor': doctor,
                                                             'checked_in_patients': checked_in_patients,
                                                             'upcoming_appointments': upcoming_appointments,
                                                             'historic_average_wait_time': historic_average_wait_time})


def get_todays_unhandled_appointments(credential, doctor):
    """
    Gets todays appointments for a given doctor that have not already been handled (i.e. cancelled, complete, no show,
    in session)
    :param credential: oauth2client credential object
    :param doctor: DrChrono doctor id
    :return:
    """
    handled_appointment_statuses = ["Cancelled", "Complete", "In Session", "No Show"]
    appointments_url = 'https://drchrono.com/api/appointments?doctor={}&date={}'.format(doctor, date.today())

    headers = {}
    credential.apply(headers)

    appointments = []
    while appointments_url:
        response = requests.get(appointments_url, headers=headers)
        response.raise_for_status()
        data = response.json()


        # TODO: should only appointments with a given freshness (i.e. not more than an hour past the appt. time) be returned?
        for appointment in data['results']:
            if appointment['patient'] is not None and not appointment['deleted_flag'] and \
                            appointment['status'] not in handled_appointment_statuses:
                appointments.append(appointment)

        appointments_url = data['next']

    return appointments


def enrich_appointments(appointments, credential):
    """
    Enriches an appointment object with information for the associated patient. Currently only returns the fields
    relevant for building the doctor dashboard.
    :param appointments:
    :param credential:
    :return:
    """
    headers = {}
    credential.apply(headers)

    # enrich appointment information with patient information
    enriched_appointments = []

    for appointment in appointments:
        patients_url = 'https://drchrono.com/api/patients/{}'.format(appointment['patient'])

        response = requests.get(patients_url, headers=headers)
        response.raise_for_status()

        patient = response.json()

        enriched_appointments.append({
            'id': appointment['id'],
            'patient': appointment['patient'],
            'scheduled_time': datetime.strptime(appointment['scheduled_time'], "%Y-%m-%dT%H:%M:%S"),
            'status': appointment['status'],
            # timestamp in ISO 8601 format, i.e. 2014-02-24T15:32:19
            'updated_at': datetime.strptime(appointment['updated_at'], "%Y-%m-%dT%H:%M:%S"),
            'first_name': patient['first_name'],
            'last_name': patient['last_name'],
        })
    return enriched_appointments


def patient_checkin(request):
    """
    Renders the patient sign-in page/form.
    :param request:
    :return:
    """
    return render(request, "checkin/patient_checkin.html")


def handle_checkin(request):
    """
    Checks the users credentials (name and SSN) and then renders some of their demographic information in the
    :param request:
    :return:
    """
    first_name = request.POST['patient-first-name']
    last_name = request.POST['patient-last-name']
    social_security_number = request.POST['patient-social-security-number']

    access_token = request.session.get('credential')
    credential = OAuth2Credentials.new_from_json(json_data=access_token) if access_token else None

    # redirect to oauth2 login flow
    if credential is None:
        return redirect("checkin:doctor-login")

    # the patients endpoint does case-insensitive and partial matching
    patients_url = 'https://drchrono.com/api/patients?first_name={}&last_name={}'.format(first_name, last_name)

    headers = {}
    credential.apply(headers)

    while patients_url:
        response = requests.get(patients_url, headers=headers)
        response.raise_for_status()
        data = response.json()

        for patient in data['results']:
            if patient['first_name'].lower() == first_name.lower() \
                    and patient['last_name'].lower() == last_name.lower() \
                    and patient['social_security_number'] == social_security_number:
                # TODO: should insurance information, custom demographics, or SSN be update-able?
                checked_in_patient = {
                    'id': patient['id'],
                    'first_name': patient['first_name'],
                    'last_name': patient['last_name'],
                    'middle_name': patient['middle_name'],
                    'date_of_birth': patient['date_of_birth'],
                    'gender': patient['gender'],
                    'address': patient['address'],
                    'cell_phone': patient['cell_phone'],
                    'city': patient['city'],
                    'email': patient['email'],
                    'emergency_contact_name': patient['emergency_contact_name'],
                    'emergency_contact_phone': patient['emergency_contact_phone'],
                    'emergency_contact_relation': patient['emergency_contact_relation'],
                    'employer': patient['employer'],
                    'employer_address': patient['employer_address'],
                    'employer_city': patient['employer_city'],
                    'employer_state': patient['employer_state'],
                    'employer_zip_code': patient['employer_zip_code'],
                    'ethnicity': patient['ethnicity'],
                    'home_phone': patient['home_phone'],
                    'preferred_language': patient['preferred_language'],
                    'race': patient['race'],
                    'responsible_party_name': patient['responsible_party_name'],
                    'responsible_party_relation': patient['responsible_party_relation'],
                    'responsible_party_phone': patient['responsible_party_phone'],
                    'responsible_party_email': patient['responsible_party_email'],
                    'state': patient['state'],
                    'zip_code': patient['zip_code'],
                }

                # TODO: add message notifying user of how many appointments they can check-in for today
                # TODO: allow selective check-in for each of the appointments
                checkin_patient_today(credential, patient['id'], request.session['doctor_id'])
                return render(request, "checkin/patient.html", context={'patient': checked_in_patient})

        patients_url = data['next']

    raise Http404()


def checkin_patient_today(credential, patient_id, doctor_id):
    # Note: cannot use the appointment ID to check-in because that is only sent to the doctor dashboard instead of the
    #   patient checkin page

    invalid_appointment_statuses = ["Cancelled", "Complete", "In Session", "No Show"]
    checked_in_status = "Arrived"

    appointments_url = 'https://drchrono.com/api/appointments?doctor={}&date={}&patient={}'.format(
        doctor_id, date.today(), patient_id)

    headers = {}
    credential.apply(headers)

    # TODO: check-in for only one appointment at a time or all in the day or all in a given time window?

    # for now, check-in all appointments for the day
    appointments = []
    while appointments_url:
        response = requests.get(appointments_url, headers=headers)
        response.raise_for_status()
        data = response.json()

        for appointment in data['results']:
            if not appointment['deleted_flag'] and appointment['status'] not in invalid_appointment_statuses:
                appointments.append(appointment)

        appointments_url = data['next']

    # check in appointments
    patched_appointment = {'status': checked_in_status}
    for appointment in appointments:
        appointments_url = 'https://drchrono.com/api/appointments/{}'.format(appointment['id'])

        response = requests.patch(appointments_url, patched_appointment, headers=headers)
        response.raise_for_status()
        # TODO: handle response status (per check-in per appointment?)



def get_patient(request, patient_id):
    access_token = request.session.get('credential')
    credential = OAuth2Credentials.new_from_json(json_data=access_token) if access_token else None

    # redirect to oauth2 login flow
    if credential is None:
        return redirect("checkin:doctor-login")

    headers = {}
    credential.apply(headers)

    patients_url = 'https://drchrono.com/api/patients/{}'.format(patient_id)

    response = requests.get(patients_url, headers=headers)
    response.raise_for_status()
    patient = response.json()

    return render(request, "checkin/patient.html", context={'patient': patient})



def update_patient(request):
    # TODO: should have some way of confirming that user ID in a patch request is the user ID of the checked-in patient

    access_token = request.session.get('credential')
    credential = OAuth2Credentials.new_from_json(json_data=access_token) if access_token else None

    # redirect to oauth2 login flow
    if credential is None:
        return redirect("checkin:doctor-login")

    patient_id = request.POST["patient-id"]

    patients_url = 'https://drchrono.com/api/patients/{}'.format(patient_id)

    headers = {}
    credential.apply(headers)

    response = requests.get(patients_url, headers=headers)
    response.raise_for_status()

    patient = response.json()

    # update all the demographics
    patient["first_name"] = request.POST["patient-first-name"]
    patient["middle_name"] = request.POST["patient-middle-name"]
    patient["last_name"] = request.POST["patient-last-name"]
    patient["date_of_birth"] = request.POST["patient-date-of-birth"]
    patient["gender"] = request.POST["patient-gender"]
    patient["ethnicity"] = request.POST["patient-ethnicity"]
    patient["race"] = request.POST["patient-race"]
    patient["address"] = request.POST["patient-address"]
    patient["city"] = request.POST["patient-city"]
    patient["state"] = request.POST["patient-state"]
    patient["zip_code"] = request.POST["patient-zip-code"]
    patient["email"] = request.POST["patient-email"]
    patient["cell_phone"] = request.POST["patient-cell-phone"]
    patient["home_phone"] = request.POST["patient-home-phone"]
    patient["preferred_language"] = request.POST["patient-preferred-language"]
    patient["emergency_contact_name"] = request.POST["patient-emergency-contact-name"]
    patient["emergency_contact_phone"] = request.POST["patient-emergency-contact-phone"]
    patient["emergency_contact_relation"] = request.POST["patient-emergency-contact-relation"]
    patient["employer"] = request.POST["patient-employer"]
    patient["employer_address"] = request.POST["patient-employer-address"]
    patient["employer_city"] = request.POST["patient-employer-city"]
    patient["employer_state"] = request.POST["patient-employer-state"]
    patient["employer_zip_code"] = request.POST["patient-employer-zip-code"]
    patient["responsible_party_name"] = request.POST["patient-responsible-party-name"]
    patient["responsible_party_relation"] = request.POST["patient-responsible-party-relation"]
    patient["responsible_party_phone"] = request.POST["patient-responsible-party-phone"]
    patient["responsible_party_email"] = request.POST["patient-responsible-party-email"]

    # patch not supported
    response = requests.put(patients_url, data=patient, headers=headers)
    response.raise_for_status()

    return HttpResponseRedirect(reverse("checkin:checkin-view"))


def start_appointment(request, appointment_id):

    patched_appointment = {'status': "In Session"}

    access_token = request.session.get('credential')
    credential = OAuth2Credentials.new_from_json(json_data=access_token) if access_token else None

    # redirect to oauth2 login flow
    if credential is None:
        return redirect("checkin:doctor-login")

    headers = {}
    credential.apply(headers)

    appointments_url = 'https://drchrono.com/api/appointments/{}'.format(appointment_id)

    # get time since the patient has checked in (currently just uses the updated_at time)
    response = requests.get(appointments_url, headers=headers)
    response.raise_for_status()
    updated_at = response.json()['updated_at']
    time_elapsed = datetime.now() - datetime.strptime(updated_at, "%Y-%m-%dT%H:%M:%S")
    minutes_elapsed = floor(time_elapsed.total_seconds() / 60)
    doctor = request.session['doctor_id']

    # update running average of time waiting per patient
    try:
        elapsed_storage = TimeWaiting.objects.get(doctor=doctor)
        elapsed_storage.minutes_waiting += minutes_elapsed
        elapsed_storage.total_patients += 1

    except TimeWaiting.DoesNotExist:
        elapsed_storage = TimeWaiting(doctor=doctor, minutes_waiting=minutes_elapsed, total_patients=1)

    elapsed_storage.save()

    response = requests.patch(appointments_url, patched_appointment, headers=headers)
    response.raise_for_status()

    return redirect("checkin:dashboard")


def auth(request):

    credential = FLOW.step2_exchange(request.GET['code'])

    # TODO: can the credential just be kept in session storage, rather than in the database?
    request.session['credential'] = credential.to_json()
    return redirect('checkin:doctor-login')
