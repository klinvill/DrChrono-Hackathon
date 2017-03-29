from __future__ import unicode_literals

from django.db import models

class TimeWaiting(models.Model):
    """
    Keeps track of the average time spent waiting by patients
    """
    doctor = models.IntegerField(primary_key=True)
    minutes_waiting = models.IntegerField()
    total_patients = models.IntegerField()

    def average_waiting_time(self):
        return self.minutes_waiting / self.total_patients
