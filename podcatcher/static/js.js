$.fn.shiftSelectable = function() {
	var lastChecked,
		$boxes = this;

	$boxes.click(function(evt) {
		if (!lastChecked) {
			lastChecked = $(this);
			return;
		}

		if (evt.shiftKey) {
			var start = $boxes.index(this);
			var end = $boxes.index(lastChecked);
			var check = lastChecked.find('input[type="checkbox"]').first().prop('checked');

			console.log(start, end, check, lastChecked);
			$boxes.slice(Math.min(start, end), Math.max(start, end) + 1).find('input[type="checkbox"]')
				.prop('checked', check)
				.trigger('change');
		}

		lastChecked = $(this);
	});
};
