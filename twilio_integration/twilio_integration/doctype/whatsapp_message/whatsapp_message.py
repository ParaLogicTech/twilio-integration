# Copyright (c) 2021, Frappe and contributors
# For license information, please see license.txt
from asyncio import Timeout

from rq.timeouts import JobTimeoutException
from six import text_type

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password
from frappe.utils import get_site_url, now_datetime
from ...twilio_handler import Twilio
import json


class WhatsAppMessage(Document):
	def on_trash(self):
		if frappe.session.user != 'Administrator':
			frappe.throw(_('Only Administrator can delete WhatsApp Message'))

	def send(self):
		client = Twilio.get_twilio_client()
		message_dict = self.get_message_dict()
		response = frappe._dict()

		try:
			response = client.messages.create(**message_dict)
			self.sent_received = 'Sent'
			self.status = response.status.title()
			self.id = response.sid
			self.send_on = response.date_sent
			self.save(ignore_permissions=True)

		except Exception as e:
			self.db_set('status', "Failed")
			self.log_error(title=_('Twilio WhatsApp Message Failed'), message=e)

	def get_message_dict(self):
		args = {
			'from_': self.from_,
			'to': self.to,
			'status_callback': '{}/api/method/twilio_integration.twilio_integration.api.whatsapp_message_status_callback'.format(
				get_site_url(frappe.local.site)
			)
		}

		if self.template_sid:
			args['content_sid'] = self.template_sid
			if self.content_variables:
				args['content_variables'] = self.content_variables
		else:
			args['body'] = self.message

		if self.media_link:
			args['media_url'] = [self.media_link]

		return args

	@classmethod
	def send_whatsapp_message(
		cls,
		receiver_list,
		message=None,
		doctype=None,
		docname=None,
		notification_type=None,
		media=None,
		communication=None,
		template_sid=None,
		content_variables=None,
		queue=None,
	):
		from frappe.email.doctype.notification.notification import get_doc_for_notification_triggers

		if are_whatsapp_messages_muted():
			frappe.msgprint(_("WhatsApp is muted"))
			return

		if isinstance(receiver_list, str):
			receiver_list = json.loads(receiver_list)
			if not isinstance(receiver_list, list):
				receiver_list = [receiver_list]

		doc = get_doc_for_notification_triggers(doctype, docname)

		run_before_send_method(doc=doc, notification_type=notification_type)

		for rec in receiver_list:
			wa_msg = cls.store_whatsapp_message(
				to=rec,
				message=message,
				doctype=doctype,
				docname=docname,
				media=media,
				communication=communication,
				template_sid=template_sid,
				content_variables=content_variables,
				queue=queue,
				notification_type=notification_type,
			)
			if queue:
				frappe.enqueue("twilio_integration.twilio_integration.doctype.whatsapp_message.whatsapp_message.send_one_wa_message",
							   message_name=wa_msg.name, enqueue_after_commit=True)
			else:
				send_one_wa_message(wa_msg.name, auto_commit=False, queue=queue)

	@classmethod
	def store_whatsapp_message(
		cls,
		to,
		message=None,
		doctype=None,
		docname=None,
		media=None,
		communication=None,
		template_sid=None,
		content_variables=None,
		queue=False,
		notification_type=None,
	):
		sender = frappe.db.get_single_value('Twilio Settings', 'whatsapp_no')
		status = 'Queued' if queue else 'Not Sent'

		wa_msg = frappe.get_doc({
			'doctype': 'WhatsApp Message',
			'from_': f'whatsapp:{sender}',
			'to': f'whatsapp:{to}',
			'message': message,
			'reference_doctype': doctype,
			'reference_document_name': docname,
			'media_link': media,
			'communication': communication,
			'status': status,
			'retry': 0,
			'notification_type': notification_type,
			'template_sid': template_sid,
			'content_variables': content_variables
		}).insert(ignore_permissions=True)

		return wa_msg


def incoming_message_callback(args):
	wa_msg = frappe.get_doc({
		'doctype': 'WhatsApp Message',
		'from_': args.From,
		'to': args.To,
		'message': args.Body,
		'profile_name': args.ProfileName,
		'sent_received': args.SmsStatus.title(),
		'id': args.MessageSid,
		'send_on': frappe.utils.now(),
		'status': 'Received'
	}).insert(ignore_permissions=True)


def run_before_send_method(doc=None, notification_type=None):
	from frappe.email.doctype.notification.notification import run_validate_notification

	if doc and notification_type:
		validation = run_validate_notification(
			doc, notification_type, throw=True
		)
		if not validation:
			frappe.throw(_("{0} Notification Validation Failed").format(notification_type))


def run_after_send_method(doctype=None, docname=None, notification_type=None):
	from frappe.core.doctype.notification_count.notification_count import add_notification_count

	if doctype and docname and notification_type:
		add_notification_count(doctype, docname, notification_type, 'WhatsApp')


def are_whatsapp_messages_muted():
	from frappe.utils import cint

	if not is_whatsapp_enabled():
		return True

	return frappe.flags.mute_whatsapp or cint(frappe.conf.get("mute_whatsapp") or 0) or False


def is_whatsapp_enabled():
	return True if frappe.get_cached_value("Twilio Settings", None, 'enabled') else False

def flush_wa_queue(queue=False):
	"""Flush queued WhatsApp Messages, called from scheduler"""
	auto_commit = not queue

	if are_whatsapp_messages_muted():
		frappe.msgprint(_("WhatsApp messages are muted"))
		return

	for message in get_queued_messages():
		send_one_wa_message(message.name, auto_commit, queue=queue)

def send_one_wa_message(message_name, auto_commit=True, queue=False):
	if are_whatsapp_messages_muted():
		frappe.msgprint(_("WhatsApp messages are muted"))
		return

	wa_message = frappe.db.sql('''
		select *
		from `tabWhatsApp Message`
		where name = %s
		for update
	''', message_name, as_dict=True)[0]

	if wa_message.status not in ('Not Sent', 'Queued'):
		if auto_commit:
			frappe.db.rollback()
		return

	frappe.db.sql("""
		update `tabWhatsApp Message` set status='Sending', modified=%s where name=%s
	""", (now_datetime(), wa_message.name), auto_commit=auto_commit)

	if wa_message.communication:
		frappe.get_doc('Communication', wa_message.communication).set_delivery_status(commit=auto_commit)

	try:
		wa_msg = frappe.get_doc('WhatsApp Message', wa_message.name)
		wa_msg.send()

		if wa_message.communication:
			frappe.get_doc('Communication', wa_message.communication).set_delivery_status(commit=auto_commit)

		run_after_send_method(wa_message.reference_doctype, wa_message.reference_document_name, wa_message.notification_type)

	except (ConnectionError, Timeout, JobTimeoutException):
		handle_timeout(wa_message, auto_commit)

	except Exception as e:
		handle_error(e, wa_message, auto_commit, queue)

def get_queued_messages():
	return frappe.db.sql('''
		select name, from_
		from `tabWhatsApp Message`
		where status IN ('Queued', 'Not Sent')
		order by priority desc, creation asc
		limit 500
	''', as_dict=True)

def clear_wa_message_queue():
	"""Remove WhatsApp messages older than 31 days with final status,
	and expire WhatsApp messages not sent for 7 days.
	Called daily via scheduler.
	"""
	final_statuses = ("Sent", "Delivered", "Read", "Failed", "Undelivered", "Not Sent")

	old_whatsapp_messages = frappe.db.sql_list("""
		SELECT name
		FROM `tabWhatsApp Message`
		WHERE status IN %s
		AND modified < (NOW() - INTERVAL '31' DAY)
	""", [final_statuses])

	if old_whatsapp_messages:
		frappe.db.sql("""
			DELETE FROM `tabWhatsApp Message`
			WHERE name IN %s
		""", [old_whatsapp_messages])

	frappe.db.sql("""
		UPDATE `tabWhatsApp Message`
		SET status='Undelivered'
		WHERE modified < (NOW() - INTERVAL '7' DAY')
		AND status='Not Sent'
	""")

def handle_timeout(wa_message, auto_commit):
	frappe.db.sql("""update `tabWhatsApp Message`
					 set status='Not Sent',
						 modified=%s
					 where name = %s""",
				  (now_datetime(), wa_message.name), auto_commit=auto_commit)

	if wa_message.communication:
		frappe.get_doc('Communication', wa_message.communication).set_delivery_status(
			commit=auto_commit)

def handle_error(e, message, auto_commit, queue):
	if auto_commit:
		frappe.db.rollback()

	if message.status == 'Queued' or message.retry < 3:
		frappe.db.sql("""
					  update `tabWhatsApp Message`
					  set status='Not Sent',
						  retry=retry+1,
						  error=%s,
						  modified=%s
					  where name = %s
					  """, (text_type(e), now_datetime(), message.name), auto_commit=auto_commit)
	else:
		frappe.db.sql("""
					  update `tabWhatsApp Message`
					  set status='Failed',
						  error=%s,
						  modified=%s
					  where name = %s
					  """, (text_type(e), now_datetime(), message.name), auto_commit=auto_commit)

	if message.communication:
		frappe.get_doc('Communication', message.communication).set_delivery_status(commit=auto_commit)

	if queue:
		print(frappe.get_traceback())
		raise e
	else:
		frappe.log_error(reference_doctype="WhatsApp Message", reference_name=message.name)
