from werkzeug.wrappers import Response

import frappe
from frappe import _
from frappe.utils import cstr
from frappe.contacts.doctype.contact.contact import get_contact_with_phone_number
from .twilio_handler import Twilio, IncomingCall, TwilioCallDetails, validate_twilio_request
from twilio_integration.twilio_integration.doctype.whatsapp_message.whatsapp_message import (
	incoming_message_callback,
	outgoing_message_status_callback
)
from twilio.twiml.messaging_response import MessagingResponse
import os


@frappe.whitelist()
def get_twilio_phone_numbers():
	twilio = Twilio.connect()
	return (twilio and twilio.get_phone_numbers()) or []


@frappe.whitelist()
def generate_access_token():
	"""Returns access token that is required to authenticate Twilio Client SDK.
	"""
	twilio = Twilio.connect()
	if not twilio:
		return {}

	from_number = frappe.db.get_value('Voice Call Settings', frappe.session.user, 'twilio_number')
	if not from_number:
		return {
			"ok": False,
			"error": "caller_phone_identity_missing",
			"detail": "Phone number is not mapped to the caller"
		}

	token=twilio.generate_voice_access_token(from_number=from_number, identity=frappe.session.user)
	return {
		'token': frappe.safe_decode(token)
	}


@frappe.whitelist(allow_guest=True)
@validate_twilio_request
def voice(**kwargs):
	"""This is a webhook called by twilio to get instructions when the voice call request comes to twilio server.
	"""
	def _get_caller_number(caller):
		identity = caller.replace('client:', '').strip()
		user = Twilio.emailid_from_identity(identity)
		return frappe.db.get_value('Voice Call Settings', user, 'twilio_number')

	args = frappe._dict(kwargs)
	twilio = Twilio.connect()
	if not twilio:
		return

	assert args.AccountSid == twilio.account_sid
	assert args.ApplicationSid == twilio.application_sid

	# Generate TwiML instructions to make a call
	from_number = _get_caller_number(args.Caller)
	resp = twilio.generate_twilio_dial_response(from_number, args.To)

	call_details = TwilioCallDetails(args, call_from=from_number)
	create_call_log(call_details)
	return Response(resp.to_xml(), mimetype='text/xml')


@frappe.whitelist(allow_guest=True)
@validate_twilio_request
def twilio_incoming_call_handler(**kwargs):
	args = frappe._dict(kwargs)
	call_details = TwilioCallDetails(args)
	create_call_log(call_details)

	resp = IncomingCall(args.From, args.To).process()
	return Response(resp.to_xml(), mimetype='text/xml')


@frappe.whitelist()
def create_call_log(call_details: TwilioCallDetails):
	call_log = frappe.get_doc({**call_details.to_dict(),
		'doctype': 'Call Log',
		'medium': 'Twilio'
	})

	call_log.flags.ignore_permissions = True
	call_log.save()
	frappe.db.commit()


@frappe.whitelist()
def update_call_log(call_sid, status=None):
	"""Update call log status.
	"""
	twilio = Twilio.connect()
	if not (twilio and frappe.db.exists("Call Log", call_sid)): return

	call_details = twilio.get_call_info(call_sid)
	call_log = frappe.get_doc("Call Log", call_sid)
	call_log.status = status or TwilioCallDetails.get_call_status(call_details.status)
	call_log.duration = call_details.duration
	call_log.flags.ignore_permissions = True
	call_log.save()
	frappe.db.commit()


@frappe.whitelist(allow_guest=True)
@validate_twilio_request
def update_recording_info(**kwargs):
	try:
		args = frappe._dict(kwargs)
		recording_url = args.RecordingUrl
		call_sid = args.CallSid
		update_call_log(call_sid)
		frappe.db.set_value("Call Log", call_sid, "recording_url", recording_url)
	except:
		frappe.log_error(title=_("Failed to capture Twilio recording"))


@frappe.whitelist()
def get_contact_details(phone):
	"""Get information about existing contact in the system.
	"""
	contact = get_contact_with_phone_number(phone.strip())
	if not contact: return
	contact_doc = frappe.get_doc('Contact', contact)
	return contact_doc and {
		'first_name': contact_doc.first_name.title(),
		'email_id': contact_doc.email_id,
		'phone_number': contact_doc.phone
	}


@frappe.whitelist(allow_guest=True)
@validate_twilio_request
def incoming_whatsapp_message_handler(**kwargs):
	"""This is a webhook called by Twilio when a WhatsApp message is received.
	"""
	args = frappe._dict(kwargs)

	response = incoming_message_callback(args)

	reply_message = None
	if cstr(response.get("reply_message")).strip():
		reply_message = response.get("reply_message")

	disable_default_reply = response.get("disable_default_reply")

	# Default Auto Reply
	if not reply_message and not disable_default_reply:
		reply_message = frappe.db.get_single_value('WhatsApp Settings', 'reply_message')

	resp = MessagingResponse()
	if reply_message:
		resp.message(reply_message)

	return Response(resp.to_xml(), mimetype='text/xml')


@frappe.whitelist(allow_guest=True)
@validate_twilio_request
def whatsapp_message_status_callback(**kwargs):
	"""This is a webhook called by Twilio whenever sent WhatsApp message status is changed.
	"""
	frappe.set_user("Administrator")
	args = frappe._dict(kwargs)
	outgoing_message_status_callback(args, auto_commit=True)


@frappe.whitelist(allow_guest=True)
@validate_twilio_request
def download_whatsapp_media(**kwargs):
	message_name = kwargs.get("message_id") or kwargs.get("message") or kwargs.get("id")
	if not message_name:
		frappe.throw(_("Message ID missing"), exc=frappe.ValidationError)

	message_doc = frappe.get_doc("WhatsApp Message", message_name)
	if message_doc.sent_received != "Sent":
		raise frappe.PermissionError

	attachment = message_doc.get_attachment(store_print_attachment=True)
	if not attachment:
		raise frappe.DoesNotExistError

	file_filters = {}
	if attachment.get("fid"):
		file_filters["name"] = attachment.get("fid")
	elif attachment.get("file_url"):
		file_filters["file_url"] = attachment.get("file_url")

	if file_filters:
		from werkzeug.utils import send_file
		import mimetypes

		file = frappe.get_doc("File", file_filters)
		media_file_path = file.get_full_path()
		if not os.path.isfile(media_file_path):
			raise frappe.DoesNotExistError

		media_filename = file.original_file_name or file.file_name
		mimetype = mimetypes.guess_type(media_filename)[0] or "application/octet-stream"

		output = open(media_file_path, "rb")
		return send_file(
			output,
			environ=frappe.local.request.environ,
			mimetype=mimetype,
			download_name=media_filename,
		)

	elif attachment.get("print_format_attachment") == 1:
		print_format_file = message_doc.get_print_format_file(attachment)
		frappe.local.response.filename = print_format_file["fname"]
		frappe.local.response.filecontent = print_format_file["fcontent"]
		frappe.local.response.type = "pdf"
	else:
		raise frappe.DoesNotExistError
