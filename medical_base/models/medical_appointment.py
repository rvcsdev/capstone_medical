from odoo import api, models, fields, _

from odoo import SUPERUSER_ID
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT

import time
from datetime import datetime

import logging
_logger = logging.getLogger(__name__)

class MedicalAppointmentStage(models.Model):
    # """ Model for case stages. This models the main stages of an appointment
    #     management flow. Main CRM objects (leads, opportunities, project
    #     issues, ...) will now use only stages, instead of state and stages.
    #     Stages are for example used to display the kanban view of records.
    # """
    _name = "medical.appointment.stage"
    _description = "Stage of Appointment"
    _rec_name = 'name'
    _order = "sequence"

    name = fields.Char(string='Stage Name', size=64, required=True, translate=True)
    sequence = fields.Integer(string='Sequence', help="Used to order stages. Lower is better.", default=1)
    requirements = fields.Text(string='Requirements')
    fold = fields.Boolean(string='Folded in Kanban View', help='This stage is folded in the kanban view when there are no records in that stage to display.', default=False)
    is_default = fields.Boolean(string='Default?', help="If checked, this stage will be selected when creating new appointments.")


class MedicalAppointment(models.Model):
    _name = 'medical.appointment'
    _inherit = ['mail.thread', 'ir.needaction_mixin']
    _description = "Medical Appointment"

    def _get_default_stage_id(self):
        # """ Gives default stage_id """
        stage_ids = self.env['medical.appointment.stage'].search([('is_default', '=', True)], order='sequence', limit=1)
        if stage_ids:
            return stage_ids[0]
        return False

    def _read_group_stage_ids(self, stages, domain, order):
        order = stages._order
        search_domain = []
        # perform search
        stage_ids = stages._search(search_domain, order=order, access_rights_uid=SUPERUSER_ID)
        result = stages.browse(stage_ids)
        return result

    @api.multi
    def name_get(self):
        res = []
        for rec in self:
            name = '[%s] %s %s' % (rec.name, rec.patient_id.name, rec.appointment_date)
            res.append((rec.id, name))
        return res

    name = fields.Char(string='Appointment ID', required=True, copy=False, readonly=True, index=True, default=lambda self: _('New'))
    # name = fields.Char(string='Appointment ID', required=True)
    STATES = {'draft': [('readonly', False)]}

    user_id = fields.Many2one('res.users', 'Responsible', readonly=True, default=lambda self: self.env.user, states=STATES)
    patient_id = fields.Many2one('medical.patient', string='Patient', required=True, select=True, help='Patient Name')
    appointment_date = fields.Datetime(string='Date and Time', required=True, default=fields.Datetime.now)
    date_end = fields.Datetime(string='do not display')
    duration = fields.Float('Duration', default=30.00, required=True)
    physician_id = fields.Many2one('medical.physician', string='Physician', select=True, required=True, help='Physician\'s Name')
    alias = fields.Char(size=256, string='Alias')
    comments = fields.Text(string='Comments')
    appointment_type = fields.Selection([('ambulatory', 'Ambulatory'), ('outpatient', 'Outpatient'),('inpatient', 'Inpatient'), ], string='Type', default='outpatient')
    institution_id = fields.Many2one('res.partner', string='Health Center', help='Medical Center', domain="[('is_institution', '=', True)]")
    # consultations = fields.Many2one('medical.physician.services', string='Consultation Services', help='Consultation Services', domain="[('physician_id', '=', physician_id)]")
    consultations = fields.Many2one(string='Consultation Service', comodel_name='product.product', required=True, ondelete="cascade", domain="[('type', '=', 'service')]")
    urgency = fields.Selection([('a', 'Normal'), ('b', 'Urgent'), ('c', 'Medical Emergency'), ], string='Urgency Level', default='a')
    specialty_id = fields.Many2one('medical.specialty', string='Specialty', help='Medical Specialty / Sector')
    stage_id = fields.Many2one('medical.appointment.stage', 'Stage', track_visibility='onchange', default=lambda self: self._get_default_stage_id(), group_expand='_read_group_stage_ids')
    current_stage = fields.Integer(related='stage_id.sequence', string='Current Stage')
    history_ids = fields.One2many('medical.appointment.history', 'appointment_id', 'History lines')

    # _group_by_full = {'stage_id': _read_group_stage_ids}

    def _get_appointments(self, cr, uid, physician_ids, institution_ids, date_start, date_end, context=None):
        # """ Get appointments between given dates, excluding pending reviewand cancelled ones """

        pending_review_id = self.pool.get('ir.model.data').get_object_reference(cr, uid, 'medical', 'stage_appointment_in_review')[1]
        cancelled_id = self.pool.get('ir.model.data').get_object_reference(cr, uid, 'medical', 'stage_appointment_canceled')[1]
        
        domain = [('physician_id', 'in', physician_ids),
                  ('date_end', '>', date_start),
                  ('appointment_date', '<', date_end),
                  ('stage_id', 'not in', [pending_review_id, cancelled_id])]

        if institution_ids:
            domain += [('institution_id', 'in', institution_ids)]

        return self.search(cr, uid, domain, context=context)

    def _set_clashes_state_to_review(self, cr, uid, physician_ids, institution_ids, date_start, date_end, context=None):
        dummy, review_stage_id = self.pool['ir.model.data'].get_object_reference(cr, uid, 'medical', 'stage_appointment_in_review')
        if not review_stage_id:
            raise orm.except_orm(_('Error!'), _('No default stage defined for review'))
        current_appointments = self._get_appointments(cr, uid, physician_ids, institution_ids, date_start, date_end, context=context)
        if current_appointments:
            self.write(cr, uid, current_appointments, {'stage_id': review_stage_id})

    @api.model
    def create(self, values):
        if values.get('name', 'New') == 'New':
            values['name'] = self.env['ir.sequence'].next_by_code('medical.appointment') or 'New'

        result = super(MedicalAppointment, self).create(values)

        # CREATE HISTORY RECORD
        appointment_history = self.env['medical.appointment.history'].create({
            'date' : time.strftime('%Y-%m-%d %H:%M:%S'),
            'appointment_id': result.id,
            'action' : "----  Created  ----"
        }) 

        return result

    @api.onchange('physician_id')
    def _get_physician_specialty(self):
        for r in self:
            r.specialty_id = r.physician_id.specialty_id

    # TO BE MOVED TO VISIT MODULE
    @api.multi
    def action_create_visit(self):
        for record in self:
            if record.appointment_type == 'outpatient':
                visit_id = self.env['medical.visit'].create({
                    'appointment_id': self.id,
                    'patient_id': self.patient_id.id,
                    'physician_id': self.physician_id.id,
                    'institution_id': self.institution_id.id,
                    'urgency': self.urgency,
                    'consultations': self.consultations.id,
                    'scheduled_start': self.appointment_date,
                })

                self.stage_id = 3

                return {
                    'type': 'ir.actions.act_window',
                    'name': 'Patient Visit',
                    'view_type': 'form',
                    'view_mode': 'form',
                    'res_model': 'medical.visit',
                    'res_id': visit_id.id,
                    'view_id': self.env.ref('medical.medical_visit_form').id,
                    # 'domain': "[('type','in',('out_invoice', 'out_refund'))]",
                    # 'context': "{'type':'out_invoice', 'journal_type': 'sale'}",
                    'target': 'current',
                }

    # TO BE MOVED TO HOSPITALIZATION MODULE
    @api.multi
    def action_create_hospitalization(self):
        for record in self:
            if record.appointment_type == 'inpatient':
                hospitalization_id = self.env['medical.patient.hospitalization'].create({
                    ''
                })
            return True

    def write(self, values):
        context = self._context or {}
        original_values = self.read(['physician_id', 'institution_id', 'appointment_date', 'date_end', 'duration'])[0]
        date_start = values.get('appointment_date', original_values['appointment_date'])
        
        result = super(MedicalAppointment, self).write(values)

        # stage change: update date_last_stage_update
        if 'stage_id' in values:
            appointment_history = self.env['medical.appointment.history']
            stage_id = self.env['medical.appointment.stage'].search([('id', '=', values['stage_id'])])
            val_history = {
                'action': "----  Changed to {0}  ----".format(stage_id.name),
                'appointment_id': self.id,
                'date': time.strftime('%Y-%m-%d %H:%M:%S'),
            }
            appointment_history.create(val_history)

            user_record = self.env['res.users'].browse(SUPERUSER_ID)
            lang_id = self.env['res.lang'].search([('code', '=', user_record.lang)])
            localized_datetime = datetime.strptime(date_start, DEFAULT_SERVER_DATETIME_FORMAT)
            context.update({'appointment_date': localized_datetime.strftime(lang_id.date_format), 'appointment_time': localized_datetime.strftime(lang_id.time_format)})

            mail_template_name = None

            if stage_id.name == 'Pending Review':
                mail_template_name = 'email_template_appointment_pending_review'
            elif stage_id.name == 'Confirm':
                mail_template_name = 'email_template_appointment_confirmation'
            elif stage_id.name == 'Canceled':
                mail_template_name = 'email_template_appointment_canceled'

            if mail_template_name:
                email_context = self.env.context.copy()
                template_res = self.env['mail.template']
                imd_res = self.env['ir.model.data']
                template_id = imd_res.get_object_reference('medical_base', mail_template_name)[1]
                template = template_res.browse(template_id)
                template.with_context(email_context).send_mail(self.id, force_send=True)

        return result

class MedicalAppointmentHistory(models.Model):
    _name = 'medical.appointment.history'
    _description = "Medical Appointment History"

    date = fields.Datetime(string='Date and Time')
    name = fields.Many2one('res.users', string='User', default=lambda self: self.env.user)
    action = fields.Text(string='Action')
    appointment_id = fields.Many2one('medical.appointment', string='History', ondelete='cascade')
