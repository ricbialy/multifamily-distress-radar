CREATE TABLE `property_leads` (
	`folio` text PRIMARY KEY NOT NULL,
	`stage` text DEFAULT 'new' NOT NULL,
	`assignee` text,
	`next_follow_up_date` text,
	`disposition` text,
	`notes` text,
	`updated_by` text NOT NULL,
	`updated_at` text DEFAULT CURRENT_TIMESTAMP NOT NULL
);
