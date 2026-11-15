CREATE TABLE `collection_receipts` (
	`session_id` text NOT NULL,
	`day` text NOT NULL,
	`page` integer NOT NULL,
	`fetched` integer NOT NULL,
	`matched` integer NOT NULL,
	`excluded` integer NOT NULL,
	`bytes` integer NOT NULL,
	`hash` text NOT NULL,
	`committed_at` text NOT NULL,
	PRIMARY KEY(`session_id`, `day`, `page`)
);
--> statement-breakpoint
CREATE TABLE `collection_sessions` (
	`id` text PRIMARY KEY NOT NULL,
	`mode` text NOT NULL,
	`from_day` text NOT NULL,
	`to_day` text NOT NULL,
	`state` text NOT NULL,
	`cursor` integer NOT NULL,
	`day` text NOT NULL,
	`page` integer NOT NULL,
	`page_size` integer NOT NULL,
	`source_total` integer,
	`fixture_total` integer NOT NULL,
	`fetched` integer NOT NULL,
	`matched` integer NOT NULL,
	`excluded` integer NOT NULL,
	`completed_days` integer NOT NULL,
	`calls` integer NOT NULL,
	`max_payload` integer NOT NULL,
	`lease` text,
	`lease_until` integer NOT NULL,
	`error` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `day_checkpoints` (
	`session_id` text NOT NULL,
	`day` text NOT NULL,
	`state` text NOT NULL,
	`next_page` integer NOT NULL,
	`total` integer NOT NULL,
	`fetched` integer NOT NULL,
	`matched` integer NOT NULL,
	`updated_at` text NOT NULL,
	PRIMARY KEY(`session_id`, `day`)
);
--> statement-breakpoint
CREATE TABLE `delivery_rows` (
	`mode` text NOT NULL,
	`source_key` text NOT NULL,
	`source_date` text NOT NULL,
	`date_basis` text NOT NULL,
	`category` text NOT NULL,
	`detail_code` text NOT NULL,
	`detail_name` text NOT NULL,
	`request_no` text NOT NULL,
	`change_order` text NOT NULL,
	`sequence` text NOT NULL,
	`request_name` text NOT NULL,
	`final_flag` text NOT NULL,
	`item_id` text NOT NULL,
	`item_name` text NOT NULL,
	`model` text NOT NULL,
	`agency` text NOT NULL,
	`agency_code` text NOT NULL,
	`region` text NOT NULL,
	`vendor` text NOT NULL,
	`quantity` text,
	`unit_price` text,
	`amount` text,
	`request_total` text,
	`raw` text NOT NULL,
	`last_session` text NOT NULL,
	`updated_at` text NOT NULL,
	PRIMARY KEY(`mode`, `source_key`)
);
--> statement-breakpoint
CREATE TABLE `seen_source_keys` (
	`session_id` text NOT NULL,
	`day` text NOT NULL,
	`source_key` text NOT NULL,
	PRIMARY KEY(`session_id`, `day`, `source_key`)
);
