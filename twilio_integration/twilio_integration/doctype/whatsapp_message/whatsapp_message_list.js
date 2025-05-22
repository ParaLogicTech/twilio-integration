frappe.listview_settings['WhatsApp Message'] = {
	get_indicator: function (doc) {
		let colour = {
			'Not Sent': 'grey',
			'Sending': 'blue',
			'Queued': 'yellow',
			'Sent': 'green',
			'Delivered': 'green',
			'Read': 'green',
			'Received': 'blue',
			'Error': 'red',
			'Undelivered': 'red',
			'Failed': 'red',
			'Expired': 'grey'
		};
		return [__(doc.status), colour[doc.status], "status,=," + doc.status];
	},
}
