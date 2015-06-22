from __future__ import absolute_import
import os
import six

from django.core.urlresolvers import reverse
from django.views.generic import TemplateView
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.utils.encoding import force_text
from django.db.models import Q
from django.template.defaultfilters import escape

from djcelery.models import TaskMeta
from celery import app, states

celery_app = app.app_or_default()

from ..models import DjanguiJob
from .. import settings as djangui_settings
from ..backend.utils import valid_user, get_file_previews
from ..django_compat import JsonResponse

def celery_status(request):
    # TODO: This function can use some sprucing up, design a better data structure for returning jobs
    spanbase = "<span class='glyphicon {}' data-toggle='tooltip' data-trigger='hover' title='{}'></span>"
    STATE_MAPPER = {
        DjanguiJob.COMPLETED: spanbase.format('glyphicon-ok', _('Success')),
        DjanguiJob.RUNNING: spanbase.format('glyphicon-refresh spinning', _('Executing')),
        states.PENDING: spanbase.format('glyphicon-time', _('In queue')),
        states.REVOKED: spanbase.format('glyphicon-stop', _('Halted')),
        DjanguiJob.SUBMITTED: spanbase.format('glyphicon-hourglass', _('Waiting to be queued'))
    }
    user = request.user
    if user.is_superuser:
        jobs = DjanguiJob.objects.all()
    else:
        jobs = DjanguiJob.objects.filter(Q(user=None) | Q(user=user) if request.user.is_authenticated() else Q(user=None))
        jobs = jobs.exclude(status=DjanguiJob.DELETED)
    # divide into user and anon jobs
    def get_job_list(job_query):
        return [{'job_name': escape(job.job_name), 'job_status': STATE_MAPPER.get(job.status, job.status),
                'job_submitted': job.created_date.strftime('%b %d %Y, %H:%M:%S'),
                'job_id': job.pk,
                 'job_description': escape(six.u('Script: {}\n{}').format(job.script.script_name, job.job_description)),
                'job_url': reverse('djangui:celery_results_info', kwargs={'job_id': job.pk})} for job in job_query]
    d = {'user': get_job_list([i for i in jobs if i.user == user]),
         'anon': get_job_list([i for i in jobs if i.user == None or (user.is_superuser and i.user != user)])}
    return JsonResponse(d, safe=False)


def celery_task_command(request):

    command = request.POST.get('celery-command')
    job_id = request.POST.get('job-id')
    job = DjanguiJob.objects.get(pk=job_id)
    response = {'valid': False,}
    valid = valid_user(job.script, request.user)
    if valid.get('valid') is True:
        user = request.user if request.user.is_authenticated() else None
        if user == job.user or job.user == None:
            if command == 'resubmit':
                new_job = job.submit_to_celery(resubmit=True, user=request.user)
                response.update({'valid': True, 'extra': {'task_url': reverse('djangui:celery_results_info', kwargs={'job_id': new_job.pk})}})
            elif command == 'rerun':
                job.submit_to_celery(user=request.user, rerun=True)
                response.update({'valid': True, 'redirect': reverse('djangui:celery_results_info', kwargs={'job_id': job_id})})
            elif command == 'clone':
                response.update({'valid': True, 'redirect': '{0}?job_id={1}'.format(reverse('djangui:djangui_task_launcher'), job_id)})
            elif command == 'delete':
                job.status = DjanguiJob.DELETED
                job.save()
                response.update({'valid': True, 'redirect': reverse('djangui:djangui_home')})
            elif command == 'stop':
                celery_app.control.revoke(job.celery_id, signal='SIGKILL', terminate=True)
                job.status = states.REVOKED
                job.save()
                response.update({'valid': True, 'redirect': reverse('djangui:celery_results_info', kwargs={'job_id': job_id})})
            else:
                response.update({'errors': {'__all__': [force_text(_("Unknown Command"))]}})
    else:
        response.update({'errors': {'__all__': [force_text(valid.get('error'))]}})
    return JsonResponse(response)


class CeleryTaskView(TemplateView):
    template_name = 'djangui/tasks/task_view.html'

    def get_context_data(self, **kwargs):
        ctx = super(CeleryTaskView, self).get_context_data(**kwargs)
        job_id = ctx.get('job_id')
        try:
            djangui_job = DjanguiJob.objects.get(pk=job_id)
        except DjanguiJob.DoesNotExist:
            ctx['task_error'] = _('This task does not exist.')
        else:
            user = self.request.user
            user = None if not user.is_authenticated() and djangui_settings.DJANGUI_ALLOW_ANONYMOUS else user
            job_user = djangui_job.user
            if job_user == None or job_user == user or (user != None and user.is_superuser):
                out_files = get_file_previews(djangui_job)
                all = out_files.pop('all', [])
                archives = out_files.pop('archives', [])
                ctx['task_info'] = {
                        'all_files': all,
                        'archives': archives,
                        'file_groups': out_files,
                        'status': djangui_job.status,
                        'last_modified': djangui_job.modified_date,
                        'job': djangui_job
                    }
            else:
                ctx['task_error'] = _('You are not authenticated to view this job.')
        return ctx

