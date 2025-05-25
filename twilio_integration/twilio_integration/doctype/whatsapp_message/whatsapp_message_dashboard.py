
from frappe import _


def get_data():
	return {
		'fieldname': 'context_message',
		'transactions': [
			{
				'label': _('Replies'),
				'items': ['WhatsApp Message']
			},
		]
	}
