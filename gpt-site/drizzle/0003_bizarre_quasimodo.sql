ALTER TABLE `delivery_rows` ADD `is_active` integer DEFAULT 1 NOT NULL;--> statement-breakpoint
ALTER TABLE `delivery_rows` ADD `inactive_reason` text DEFAULT '' NOT NULL;--> statement-breakpoint
ALTER TABLE `delivery_rows` ADD `change_rank` text DEFAULT '0' NOT NULL;--> statement-breakpoint
ALTER TABLE `delivery_rows` ADD `vendor_bizno` text DEFAULT '' NOT NULL;--> statement-breakpoint
ALTER TABLE `delivery_rows` ADD `contract_no` text DEFAULT '' NOT NULL;--> statement-breakpoint
CREATE INDEX `idx_delivery_current` ON `delivery_rows` (`mode`,`is_active`,`request_no`,`sequence`,`change_rank`);--> statement-breakpoint
UPDATE delivery_rows SET change_rank=COALESCE(NULLIF(ltrim(change_order,'0'),''),'0');
