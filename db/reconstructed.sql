-- РЕКОНСТРУКЦІЯ (наша), а не копія зі старого сайту.
-- Власні таблиці й колонки старої ERP, для яких DDL у коді не знайдено.
-- Структуру відновлено з того, які поля пише та читає PHP-код старого сайту.
-- Застосовувати після db/schema.sql.

-- Журнал змін статусів замовлення (у старій системі вже існувала).
CREATE TABLE `dd_order_status_log` (
  `order_status_log_id` int(11) NOT NULL AUTO_INCREMENT,
  `order_id` int(11) NOT NULL,
  `user_id` int(11) NOT NULL,
  `old_status_id` int(11) NOT NULL,
  `new_status_id` int(11) NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`order_status_log_id`),
  KEY `order_id` (`order_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Довідник джерел замовлення (сайт, Instagram, дзвінок...), багатомовний як order_status.
CREATE TABLE `dd_order_source` (
  `order_source_id` int(11) NOT NULL AUTO_INCREMENT,
  `language_id` int(11) NOT NULL,
  `name` varchar(64) NOT NULL,
  PRIMARY KEY (`order_source_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Власна колонка замовлення: джерело. 0 = не вказано.
ALTER TABLE `dd_order`
  ADD COLUMN `order_source_id` int(11) NOT NULL DEFAULT '0';

-- ═══ Склад розхідників (модуль StockTrack старого сайту) ═══
-- Колонки взято із запитів admin/model/tool/stocktrack.php. Типи, довжини й рушій вгадані:
-- DDL модуля в коді немає, тож їх треба звірити зі справжньою базою (SHOW CREATE TABLE).
-- InnoDB обрано, бо модуль створював таблиці сам, а MySQL 5.5+ без явного ENGINE створює InnoDB.

CREATE TABLE `dd_stock_categories` (
  `category_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `sort_order` int(11) NOT NULL DEFAULT '0',
  PRIMARY KEY (`category_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `dd_stock_suppliers` (
  `supplier_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(150) NOT NULL,
  `phone` varchar(32) NOT NULL DEFAULT '',
  `email` varchar(96) NOT NULL DEFAULT '',
  `categories` varchar(255) NOT NULL DEFAULT '',  -- вільний текст: що постачає
  `notes` text NOT NULL,
  PRIMARY KEY (`supplier_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- status = 0 означає «видалено»: старий модуль не видаляє позиції, а приховує.
CREATE TABLE `dd_stock_items` (
  `item_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(150) NOT NULL,
  `category_id` int(11) NULL,
  `unit` varchar(16) NOT NULL DEFAULT 'шт.',
  `stock` decimal(12,3) NOT NULL DEFAULT '0.000',
  `min_stock` decimal(12,3) NOT NULL DEFAULT '0.000',
  `used_for` varchar(255) NOT NULL DEFAULT '',
  `supplier_id` int(11) NULL,
  `price` decimal(15,4) NOT NULL DEFAULT '0.0000',
  `status` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`item_id`),
  KEY `category_id` (`category_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Журнал руху розхідників. action: 'use' (витрата) або 'restock' (прихід); quantity завжди додатна.
-- date_added старий модуль не задає явно, тож у справжній таблиці, ймовірно, DEFAULT CURRENT_TIMESTAMP.
CREATE TABLE `dd_stock_log` (
  `log_id` int(11) NOT NULL AUTO_INCREMENT,
  `item_id` int(11) NOT NULL,
  `item_name` varchar(150) NOT NULL,
  `user_id` int(11) NOT NULL DEFAULT '0',
  `username` varchar(20) NOT NULL DEFAULT '',
  `action` varchar(16) NOT NULL,
  `quantity` decimal(12,3) NOT NULL,
  `stock_before` decimal(12,3) NOT NULL,
  `stock_after` decimal(12,3) NOT NULL,
  `comment` varchar(255) NOT NULL DEFAULT '',
  `date_added` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`log_id`),
  KEY `item_id` (`item_id`, `date_added`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ═══ Виробництво (модуль StockTrack старого сайту) ═══
-- Так само відновлено із запитів admin/model/tool/stocktrack.php і catalog/controller/extension/event/stocktrack.php.
-- Типи, рушій і первинні ключі вгадані, звірити зі справжньою базою.

-- Модель виробу. type: 'clothing' (власний пошив) або 'ready_made' (готовий товар від постачальника).
-- min_total: якщо пошитих разом менше, модель потрапляє в список «треба шити». status = 0 означає «видалено».
CREATE TABLE `dd_stock_production` (
  `production_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(150) NOT NULL,
  `type` varchar(32) NOT NULL DEFAULT 'clothing',
  `min_total` int(11) NOT NULL DEFAULT '50',
  `sort_order` int(11) NOT NULL DEFAULT '0',
  `status` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`production_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Розмір моделі: скільки розкроєно (cut_stock) і пошито (sewn_stock).
-- oc_option_value_id зв'язує розмір з опцією товару OpenCart (dd_option_value).
CREATE TABLE `dd_stock_production_sizes` (
  `size_id` int(11) NOT NULL AUTO_INCREMENT,
  `production_id` int(11) NOT NULL,
  `size_name` varchar(32) NOT NULL,
  `percent` decimal(5,2) NOT NULL DEFAULT '0.00',
  `cut_stock` decimal(12,3) NOT NULL DEFAULT '0.000',
  `sewn_stock` decimal(12,3) NOT NULL DEFAULT '0.000',
  `sort_order` int(11) NOT NULL DEFAULT '0',
  `oc_option_value_id` int(11) NULL,
  PRIMARY KEY (`size_id`),
  KEY `production_id` (`production_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Товари OpenCart, прив'язані до моделі напряму.
CREATE TABLE `dd_stock_production_products` (
  `production_id` int(11) NOT NULL,
  `product_id` int(11) NOT NULL,
  PRIMARY KEY (`production_id`, `product_id`),
  KEY `product_id` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Прив'язка через категорію OpenCart: усі товари категорії, назва яких містить name_filter
-- (або точно дорівнює йому, якщо match_exact = 1). Порожній фільтр — уся категорія.
CREATE TABLE `dd_stock_production_categories` (
  `production_id` int(11) NOT NULL,
  `oc_category_id` int(11) NOT NULL,
  `name_filter` varchar(255) NOT NULL DEFAULT '',
  `match_exact` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`production_id`, `oc_category_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Компонент крою (деталь: перед, спинка, рукав...).
CREATE TABLE `dd_stock_production_components` (
  `component_id` int(11) NOT NULL AUTO_INCREMENT,
  `production_id` int(11) NOT NULL,
  `name` varchar(100) NOT NULL,
  `sort_order` int(11) NOT NULL DEFAULT '0',
  PRIMARY KEY (`component_id`),
  KEY `production_id` (`production_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Запас розкроєних деталей за розміром.
CREATE TABLE `dd_stock_production_component_stock` (
  `component_id` int(11) NOT NULL,
  `size_id` int(11) NOT NULL,
  `stock` decimal(12,3) NOT NULL DEFAULT '0.000',
  PRIMARY KEY (`component_id`, `size_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Специфікація крою: скільки розхідника йде на одну деталь.
-- size_id NULL — для всіх розмірів; рядок із конкретним size_id перекриває його для цього розміру.
-- bom_id — НАШ ключ: старий модуль ним не користується (пише через DELETE + INSERT за іменами полів),
-- тож якщо в справжній таблиці його немає, його можна додати без шкоди для старого сайту.
CREATE TABLE `dd_stock_production_component_bom` (
  `bom_id` int(11) NOT NULL AUTO_INCREMENT,
  `component_id` int(11) NOT NULL,
  `size_id` int(11) NULL,
  `item_id` int(11) NOT NULL,
  `qty_per_unit` decimal(12,4) NOT NULL,
  PRIMARY KEY (`bom_id`),
  KEY `component_id` (`component_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Специфікація пошиву: на один пошитий виріб ідуть деталі крою (component_id) і прямі розхідники (item_id: нитки, бірки).
-- Перекриття за size_id і ключ bom_id — як у специфікації крою.
CREATE TABLE `dd_stock_production_sewn_bom` (
  `bom_id` int(11) NOT NULL AUTO_INCREMENT,
  `production_id` int(11) NOT NULL,
  `size_id` int(11) NULL,
  `component_id` int(11) NULL,
  `item_id` int(11) NULL,
  `qty_per_unit` decimal(12,4) NOT NULL,
  PRIMARY KEY (`bom_id`),
  KEY `production_id` (`production_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Журнал виробництва: кожна зміна залишку крою (stage = 'cut') чи пошиву ('sewn') за розміром.
-- direction: 'in' (+Покроєно, +Пошито) або 'out' (В пошив, Відвантаження, замовлення).
-- Замовлення старий сайт пише з user_id = 0 і username = 'system'.
CREATE TABLE `dd_stock_production_log` (
  `log_id` int(11) NOT NULL AUTO_INCREMENT,
  `production_id` int(11) NOT NULL,
  `size_id` int(11) NOT NULL,
  `size_name` varchar(32) NOT NULL,
  `user_id` int(11) NOT NULL DEFAULT '0',
  `username` varchar(20) NOT NULL DEFAULT '',
  `stage` varchar(8) NOT NULL,
  `direction` varchar(8) NOT NULL,
  `quantity` decimal(12,3) NOT NULL,
  `stock_before` decimal(12,3) NOT NULL,
  `stock_after` decimal(12,3) NOT NULL,
  `comment` varchar(255) NOT NULL DEFAULT '',
  `date_added` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`log_id`),
  KEY `production_id` (`production_id`, `date_added`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ═══ Журнал SMS ═══
-- Відновлено з admin/model/tool/log.php (addSMSLog). pre_order_id — для передзамовлень, 0 якщо немає.
-- response — сирий текст відповіді SMS-сервісу. Типи вгадані, звірити.
CREATE TABLE `dd_sms_log` (
  `message_id` int(11) NOT NULL AUTO_INCREMENT,
  `order_id` int(11) NOT NULL DEFAULT '0',
  `pre_order_id` int(11) NOT NULL DEFAULT '0',
  `phone` varchar(32) NOT NULL,
  `text` text NOT NULL,
  `response` text NOT NULL,
  `date` datetime NOT NULL,
  PRIMARY KEY (`message_id`),
  KEY `order_id` (`order_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Власна колонка статусу замовлення: колір у списку замовлень (#rrggbb). Є в справжній базі (видно в phpMyAdmin),
-- у стандартному OpenCart її немає. Тип вгаданий.
ALTER TABLE `dd_order_status`
  ADD COLUMN `color` varchar(7) NOT NULL DEFAULT '';

-- ═══ Доставка: ТТН Нової Пошти ═══
-- Відновлено з admin/model/novaposhta/document.php, admin/controller/novaposhta/document.php
-- і admin/model/novaposhta_international/document.php. Типи й рушій вгадані, звірити.
-- Одна ТТН на замовлення. carrier: порожньо або 'novaposhta' — Нова Пошта, 'international' — Nova Post International.
-- status_code — код статусу з API Нової Пошти (9 — отримано, 7/8 — на відділенні, 102–108 — відмова чи повернення...).
-- date_payed_keeping — з якої дати зберігання на відділенні стає платним.
-- api_key: старий сайт зберігає тут ключ API відправника для кожної ТТН (для друку й видалення); NewErp цю колонку не читає й не пише.
CREATE TABLE `dd_novaposhta_document` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `order_id` int(11) NOT NULL,
  `carrier` varchar(32) NOT NULL DEFAULT '',
  `ref` varchar(64) NOT NULL DEFAULT '',
  `api_key` varchar(64) NOT NULL DEFAULT '',
  `number` varchar(32) NOT NULL DEFAULT '',
  `cost` decimal(15,4) NOT NULL DEFAULT '0.0000',
  `status` varchar(255) NOT NULL DEFAULT '',
  `status_code` int(11) NOT NULL DEFAULT '0',
  `recipient_address` varchar(255) NOT NULL DEFAULT '',
  `redelivery` tinyint(1) NOT NULL DEFAULT '0',
  `date_created` datetime NULL,
  `date_scheduled` date NULL,
  `date_payed_keeping` date NULL,
  PRIMARY KEY (`id`),
  KEY `order_id` (`order_id`),
  KEY `number` (`number`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Власна колонка замовлення: коли клієнту нагадали забрати посилку, доки зберігання не стало платним.
ALTER TABLE `dd_order`
  ADD COLUMN `date_remind` datetime NULL;

-- ═══ Доставка: довідник відділень Нової Пошти ═══
-- Відновлено з admin/model/novaposhta/warehouse.php і catalog/model/address/novaposhta.php. Типи, ключі й рушій вгадані, звірити.
-- Таблицею користується й вітрина старого сайту (вибір міста й відділення при оформленні), тому NewErp оновлює саме її.
-- Окремої таблиці міст немає: старий сайт бере міста з цієї ж таблиці (GROUP BY city_ref), а dd_novaposhta_city не використовується.
-- sort_order — номер відділення з назви («№12» → 12); status — 1, якщо відділення працює і це відділення, поштомат чи пункт.
CREATE TABLE `dd_novaposhta_warehouse` (
  `ref` varchar(36) NOT NULL,
  `description` varchar(255) NOT NULL DEFAULT '',
  `category_of_warehouse` varchar(32) NOT NULL DEFAULT '',
  `city_ref` varchar(36) NOT NULL DEFAULT '',
  `city_description` varchar(255) NOT NULL DEFAULT '',
  `weight` int(11) NOT NULL DEFAULT 0,
  `sort_order` int(11) NOT NULL DEFAULT 0,
  `status` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`ref`),
  KEY `city_ref` (`city_ref`),
  KEY `city_description` (`city_description`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Власна колонка замовлення: завдаток (передоплата), уже отриманий до відправки. Старий сайт віднімає його
-- від суми фіскального чека (admin/model/cashbox/cheque.php) і накладеного платежу. Тип вгаданий, звірити.
ALTER TABLE `dd_order`
  ADD COLUMN `prepayment` decimal(15,4) NOT NULL DEFAULT '0.0000';

-- ═══ Каса: ПРРО Checkbox ═══
-- Відновлено з admin/model/cashbox/*.php, admin/controller/cashbox/*.php і system/library/Checkbox.php.
-- Типи, довжини, ключі й рушій вгадані, звірити (SHOW CREATE TABLE).

-- Реквізити (ФОП), на які видається чек.
CREATE TABLE `dd_cashbox_requisites` (
  `requisites_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL DEFAULT '',
  `description` text NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`requisites_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Склад або точка продажу каси.
CREATE TABLE `dd_cashbox_warehouse` (
  `warehouse_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL DEFAULT '',
  `description` text NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`warehouse_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Касир Checkbox. payment_code — для яких замовлень (код оплати OpenCart) його обирати.
-- auto_close_time — о котрій закривати зміну автоматично (Z-звіт).
-- login, password, checkbox_key (ключ ліцензії каси) й access_token старий сайт зберігає тут відкрито.
-- NewErp ці чотири колонки не читає й не пише: ключі касирів лише в dotnet user-secrets (Checkbox:Cashiers).
CREATE TABLE `dd_cashbox_user` (
  `user_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL DEFAULT '',
  `description` text NOT NULL,
  `payment_code` varchar(128) NOT NULL DEFAULT '',
  `login` varchar(255) NOT NULL DEFAULT '',
  `password` varchar(255) NOT NULL DEFAULT '',
  `checkbox_key` varchar(255) NOT NULL DEFAULT '',
  `auto_close_time` time NULL,
  `access_token` text NULL,
  `date_access_token_update` datetime NULL,
  `customer_id` int(11) NOT NULL DEFAULT '0',
  `warehouse_id` int(11) NOT NULL DEFAULT '0',
  `requisites_id` int(11) NOT NULL DEFAULT '0',
  `status` tinyint(1) NOT NULL DEFAULT '0',
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Робоча зміна касира. user_id — хто відкрив (dd_user), date_end NULL — зміна відкрита.
CREATE TABLE `dd_cashbox_work_shift` (
  `work_shift_id` int(11) NOT NULL AUTO_INCREMENT,
  `user_id` int(11) NOT NULL DEFAULT '0',
  `cashbox_user_id` int(11) NOT NULL,
  `checkbox_shift_id` varchar(64) NOT NULL DEFAULT '',
  `date_added` datetime NOT NULL,
  `date_start` datetime NOT NULL,
  `date_end` datetime NULL,
  PRIMARY KEY (`work_shift_id`),
  KEY `cashbox_user_id` (`cashbox_user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Фіскальний чек до замовлення. checkbox_id — id чека в Checkbox (порожньо, поки не фіскалізовано).
-- sum старий сайт пише через (int), тобто без копійок; можливо, і колонка цілочисельна — звірити.
CREATE TABLE `dd_cashbox_cheque` (
  `cheque_id` int(11) NOT NULL AUTO_INCREMENT,
  `order_id` int(11) NOT NULL,
  `cashbox_user_id` int(11) NOT NULL,
  `cashbox_warehouse_id` int(11) NOT NULL DEFAULT '0',
  `cashbox_requisites_id` int(11) NOT NULL DEFAULT '0',
  `sum` decimal(15,4) NOT NULL DEFAULT '0.0000',
  `checkbox_id` varchar(64) NOT NULL DEFAULT '',
  `cashbox_work_shift_id` int(11) NOT NULL DEFAULT '0',
  `cashbox_report_id` int(11) NOT NULL DEFAULT '0',
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`cheque_id`),
  KEY `order_id` (`order_id`),
  KEY `cashbox_user_id` (`cashbox_user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Z-звіт (закриття зміни). z_report_id — id звіту в Checkbox.
CREATE TABLE `dd_cashbox_report` (
  `report_id` int(11) NOT NULL AUTO_INCREMENT,
  `user_id` int(11) NOT NULL DEFAULT '0',
  `cashbox_user_id` int(11) NOT NULL,
  `z_report_id` varchar(64) NOT NULL DEFAULT '',
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`report_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ═══ Надходження на рахунки ПриватБанку ═══
-- Відновлено з admin/model/sale/privatbank.php і admin/controller/sale/privatbank.php. Типи вгадані, звірити.
-- Суми старий сайт зберігає текстом із валютою («1500.00 UAH»). appcode — id транзакції в банку (не повторюється).
-- order_id — замовлення, до якого прив'язано платіж (0 — ще не прив'язано); show_status = 0 — платіж приховано.
-- status пише лише метод updateStatus, якого в моделі старого сайту немає; NewErp колонку не використовує.
CREATE TABLE `dd_privatbank` (
  `payment_id` int(11) NOT NULL AUTO_INCREMENT,
  `order_id` int(11) NOT NULL DEFAULT '0',
  `appcode` varchar(64) NOT NULL DEFAULT '',
  `card` varchar(64) NOT NULL DEFAULT '',
  `amount` varchar(32) NOT NULL DEFAULT '',
  `cardamount` varchar(32) NOT NULL DEFAULT '',
  `rest` varchar(32) NOT NULL DEFAULT '',
  `description` text NOT NULL,
  `terminal` varchar(255) NOT NULL DEFAULT '',
  `trandate` date NOT NULL,
  `trantime` time NOT NULL,
  `status` varchar(32) NOT NULL DEFAULT '',
  `show_status` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`payment_id`),
  KEY `appcode` (`appcode`),
  KEY `order_id` (`order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ═══ Власні колонки замовлення, які пише оформлення замовлення старого сайту ═══
-- Відновлено з catalog/model/checkout/order.php (addOrder) і admin/model/sale/order.php. Типи й значення за замовчуванням вгадані, звірити.
-- Instagram клієнта, будинок і квартира для адресної доставки, день народження (для привітань), «передзвоніть мені».
ALTER TABLE `dd_order`
  ADD COLUMN `instagram` varchar(128) NOT NULL DEFAULT '',
  ADD COLUMN `birthday_day` int(2) NOT NULL DEFAULT '0',
  ADD COLUMN `birthday_month` int(2) NOT NULL DEFAULT '0',
  ADD COLUMN `shipping_house` varchar(32) NOT NULL DEFAULT '',
  ADD COLUMN `shipping_flat` varchar(32) NOT NULL DEFAULT '',
  ADD COLUMN `callback` tinyint(1) NOT NULL DEFAULT '0';

-- Товар додано до замовлення як супутній (cross-sell). Звірити.
ALTER TABLE `dd_order_product`
  ADD COLUMN `crosssell` tinyint(1) NOT NULL DEFAULT '0';

-- ═══ Партнери-перепродавці та фід для них ═══
-- Відновлено з admin/model/sale/partner.php і catalog/model/feed/eva.php. DDL у коді немає: типи, довжини й ключі вгадані, звірити.
-- code — короткий код партнера, з ним формується адреса фіда; default_quantity — кількість, яку фід показує для кожної пропозиції.
CREATE TABLE `dd_partner` (
  `partner_id` int(11) NOT NULL AUTO_INCREMENT,
  `code` varchar(32) NOT NULL,
  `name` varchar(128) NOT NULL,
  `status` tinyint(1) NOT NULL DEFAULT '1',
  `feed_url` varchar(255) NOT NULL DEFAULT '',
  `feed_enabled` tinyint(1) NOT NULL DEFAULT '0',
  `vendor_name` varchar(64) NOT NULL DEFAULT '',
  `notes` text NOT NULL,
  `default_quantity` int(11) NOT NULL DEFAULT '5',
  `date_added` datetime NOT NULL,
  `date_modified` datetime NOT NULL,
  PRIMARY KEY (`partner_id`),
  UNIQUE KEY `code` (`code`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Категорія магазину (за потреби лише один розмір) → категорія партнера, з націнкою до ціни (percent_supply)
-- і до перекресленої «старої» ціни (percent_sale) у відсотках. NULL — без націнки чи без старої ціни. Звірити.
CREATE TABLE `dd_partner_category_map` (
  `partner_category_map_id` int(11) NOT NULL AUTO_INCREMENT,
  `partner_id` int(11) NOT NULL,
  `category_id` int(11) NOT NULL,
  `size_name` varchar(64) DEFAULT NULL,
  `external_id` varchar(64) NOT NULL DEFAULT '',
  `external_name` varchar(255) NOT NULL DEFAULT '',
  `percent_supply` decimal(8,2) DEFAULT NULL,
  `percent_sale` decimal(8,2) DEFAULT NULL,
  PRIMARY KEY (`partner_category_map_id`),
  KEY `partner_id` (`partner_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Окремий товар (за потреби лише один розмір) у фіді партнера: свій артикул і назва в партнера, фіксовані ціни.
-- Ключа partner_product_map_id старий код не читає, але таблиці потрібен первинний ключ. Звірити.
CREATE TABLE `dd_partner_product_map` (
  `partner_product_map_id` int(11) NOT NULL AUTO_INCREMENT,
  `partner_id` int(11) NOT NULL,
  `product_id` int(11) NOT NULL,
  `size_name` varchar(64) DEFAULT NULL,
  `external_id` varchar(64) NOT NULL DEFAULT '',
  `external_name` varchar(255) NOT NULL DEFAULT '',
  `price_supply` decimal(15,4) DEFAULT NULL,
  `price_sale` decimal(15,4) DEFAULT NULL,
  PRIMARY KEY (`partner_product_map_id`),
  KEY `partner_id` (`partner_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- ═══ Порядок товарів за продажами ═══
-- Відновлено з admin/model/catalog/product_order.php, label.php і crosssell.php. DDL у коді немає: типи, довжини, ключі й рушій вгадані, звірити.
-- Хто створив замовлення в адмінці. Порядок товарів не рахує продажі одного користувача (B2B). Звірити.
ALTER TABLE `dd_order`
  ADD COLUMN `user_id` int(11) NOT NULL DEFAULT '0';

-- Мітки товарів («Новинка», «Хіт»...).
CREATE TABLE `dd_product_label` (
  `label_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(64) NOT NULL,
  `background_color` varchar(16) NOT NULL DEFAULT '',
  `text_color` varchar(16) NOT NULL DEFAULT '',
  `sort_order` int(3) NOT NULL DEFAULT '0',
  `status` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`label_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_product_to_label` (
  `product_id` int(11) NOT NULL,
  `label_id` int(11) NOT NULL,
  PRIMARY KEY (`product_id`,`label_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Панель допродажу при оформленні: окремі товари (type = 'product') і категорії (type = 'category') зі знижкою, %.
CREATE TABLE `dd_crosssell_item` (
  `crosssell_item_id` int(11) NOT NULL AUTO_INCREMENT,
  `type` varchar(16) NOT NULL,
  `target_id` int(11) NOT NULL,
  `discount` decimal(5,2) NOT NULL DEFAULT '0.00',
  PRIMARY KEY (`crosssell_item_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Власний порядок товарів у панелі допродажу.
CREATE TABLE `dd_crosssell_sort` (
  `product_id` int(11) NOT NULL,
  `sort_order` int(11) NOT NULL,
  PRIMARY KEY (`product_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Знімки sort_order усіх товарів перед кожним перерахунком (для відкату).
CREATE TABLE `dd_product_order_snapshot` (
  `snapshot_id` int(11) NOT NULL AUTO_INCREMENT,
  `note` varchar(255) NOT NULL DEFAULT '',
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`snapshot_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_product_order_snapshot_item` (
  `snapshot_id` int(11) NOT NULL,
  `product_id` int(11) NOT NULL,
  `sort_order` int(11) NOT NULL,
  PRIMARY KEY (`snapshot_id`,`product_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Закріплені вручну позиції: товар product_id стоїть на позиції position у групі (group_type, group_key).
CREATE TABLE `dd_product_manual_pin` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `group_type` varchar(32) NOT NULL,
  `group_key` varchar(128) NOT NULL,
  `position` int(11) NOT NULL,
  `product_id` int(11) NOT NULL,
  `created_at` datetime NOT NULL,
  PRIMARY KEY (`id`),
  KEY `group` (`group_type`,`group_key`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- ═══ Передзамовлення ═══
-- Відновлено з admin/model/catalog/product.php, system/library/cart/cart.php і admin/controller/tool/console.php (updatePreOrdersQuantity).
-- pre_order_quantity — скільки штук розміру можна продати наперед, коли залишку немає; date_available — коли розмір очікується.
-- Типи вгадані (старий код пише дату рядком), звірити.
ALTER TABLE `dd_product_option_value`
  ADD COLUMN `pre_order_quantity` int(11) NOT NULL DEFAULT '0',
  ADD COLUMN `date_available` date DEFAULT NULL;

-- ═══ Маркетинг ═══
-- Запитання покупців про товар (форма на сайті). Відновлено з catalog/model/catalog/question.php; DDL у коді немає,
-- типи й рушій вгадані, звірити. NewErp лише читає телефони звідси для вивантаження контактів.
CREATE TABLE `dd_question` (
  `question_id` int(11) NOT NULL AUTO_INCREMENT,
  `product_id` int(11) NOT NULL DEFAULT '0',
  `name` varchar(64) NOT NULL DEFAULT '',
  `telephone` varchar(32) NOT NULL DEFAULT '',
  `question` text NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`question_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Підписка на розсилку (e-mail). Відновлено з catalog/model/catalog/subscribe.php; типи вгадані, звірити.
CREATE TABLE `dd_subscribe` (
  `subscribe_id` int(11) NOT NULL AUTO_INCREMENT,
  `email` varchar(96) NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`subscribe_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- ═══ План пошиву ═══
-- production_period — за скільки днів від дати замовлення товар цієї категорії має бути готовий.
-- Відновлено з admin/controller/sale/order.php (date_production = date_added + production_period днів); тип і значення
-- за замовчуванням вгадані, звірити.
ALTER TABLE `dd_category`
  ADD COLUMN `production_period` int(11) NOT NULL DEFAULT '0';

-- ═══ Склади й пункти видачі ═══
-- Довідник «Каталог → Склади» старої адмінки. Відновлено з admin/model/catalog/warehouse.php і warehouse_form.twig
-- (назва до 64 символів — з перевірки форми). Решта довжин, типи й рушій вгадані, звірити.
CREATE TABLE `dd_warehouse` (
  `warehouse_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(64) NOT NULL,
  `description` text NOT NULL,
  `contact` varchar(128) NOT NULL DEFAULT '',
  `telephone` varchar(255) NOT NULL DEFAULT '',
  `city` varchar(128) NOT NULL DEFAULT '',
  `address` varchar(255) NOT NULL DEFAULT '',
  `type` varchar(64) NOT NULL DEFAULT '',
  `image` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`warehouse_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- ═══ Акційні ціни товарів ═══

-- Стандартна таблиця OpenCart, якої немає в нашому дампі; старий сайт додав колонку `percent`:
-- знижка у відсотках від ціни товару (тоді `price` не діє), ціну округлено до десятків гривень.
-- Без NO_ZERO_DATE лише в цьому сеансі, інакше MySQL 8 не прийме DEFAULT '0000-00-00' (як у schema.sql).
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';

CREATE TABLE `dd_product_special` (
  `product_special_id` int(11) NOT NULL AUTO_INCREMENT,
  `product_id` int(11) NOT NULL,
  `customer_group_id` int(11) NOT NULL,
  `priority` int(5) NOT NULL DEFAULT '1',
  `price` decimal(15,4) NOT NULL DEFAULT '0.0000',
  `percent` decimal(5,2) NOT NULL DEFAULT '0.00',
  `date_start` date NOT NULL DEFAULT '0000-00-00',
  `date_end` date NOT NULL DEFAULT '0000-00-00',
  PRIMARY KEY (`product_special_id`),
  KEY `product_id` (`product_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- ═══ Купони: на що діють і на що ні ═══

-- Стандартні таблиці OpenCart, яких немає в нашому дампі.
CREATE TABLE `dd_coupon_product` (
  `coupon_product_id` int(11) NOT NULL AUTO_INCREMENT,
  `coupon_id` int(11) NOT NULL,
  `product_id` int(11) NOT NULL,
  PRIMARY KEY (`coupon_product_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_coupon_category` (
  `coupon_id` int(11) NOT NULL,
  `category_id` int(11) NOT NULL,
  PRIMARY KEY (`coupon_id`,`category_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_coupon_history` (
  `coupon_history_id` int(11) NOT NULL AUTO_INCREMENT,
  `coupon_id` int(11) NOT NULL,
  `order_id` int(11) NOT NULL,
  `customer_id` int(11) NOT NULL,
  `amount` decimal(15,4) NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`coupon_history_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Власні таблиці старого сайту (admin/model/marketing/coupon.php): товари й категорії, на які купон не діє.
CREATE TABLE `dd_coupon_product_exclude` (
  `coupon_id` int(11) NOT NULL,
  `product_id` int(11) NOT NULL,
  PRIMARY KEY (`coupon_id`,`product_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_coupon_category_exclude` (
  `coupon_id` int(11) NOT NULL,
  `category_id` int(11) NOT NULL,
  PRIMARY KEY (`coupon_id`,`category_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- ═══ Ремаркетинг: звідки прийшло замовлення й коли пішли події пікселів ═══

-- Таблиця модуля «Remarketing All in One» старого сайту; вітрина пише її при оформленні замовлення. Колонки відновлено
-- з admin/controller/sale/order.php (картка замовлення) і admin/model/report/remarketing.php (звіт UTM); у модуля
-- можуть бути й інші колонки, звірити tools/check_live.py. Час події '0000-00-00 00:00:00' — подію не надсилали.
-- Без NO_ZERO_DATE лише в цьому сеансі, інакше MySQL 8 не прийме такі значення за замовчуванням (як у schema.sql).
SET SESSION sql_mode = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';

CREATE TABLE `dd_remarketing_orders` (
  `order_id` int(11) NOT NULL,
  `first_referrer` varchar(255) NOT NULL DEFAULT '',
  `last_referrer` varchar(255) NOT NULL DEFAULT '',
  `utm_source` varchar(255) NOT NULL DEFAULT '',
  `utm_medium` varchar(255) NOT NULL DEFAULT '',
  `utm_campaign` varchar(255) NOT NULL DEFAULT '',
  `utm_content` varchar(255) NOT NULL DEFAULT '',
  `utm_term` varchar(255) NOT NULL DEFAULT '',
  `utm_referrer` varchar(255) NOT NULL DEFAULT '',
  `success_page` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `ga4` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `facebook` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `facebook_lead` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `esputnik` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `telegram` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  `tiktok` datetime NOT NULL DEFAULT '0000-00-00 00:00:00',
  PRIMARY KEY (`order_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- ═══ Дані вітрини для звітів: хто на сайті, бонусні бали ═══

-- Стандартні таблиці OpenCart, яких немає в нашому дампі. customer_online вітрина заповнює, якщо в налаштуваннях
-- увімкнено «Customers Online», і сама видаляє записи, старші за годину.
CREATE TABLE `dd_customer_online` (
  `ip` varchar(40) NOT NULL,
  `customer_id` int(11) NOT NULL,
  `url` text NOT NULL,
  `referer` text NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`ip`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_customer_reward` (
  `customer_reward_id` int(11) NOT NULL AUTO_INCREMENT,
  `customer_id` int(11) NOT NULL DEFAULT '0',
  `order_id` int(11) NOT NULL DEFAULT '0',
  `description` text NOT NULL,
  `points` int(8) NOT NULL DEFAULT '0',
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`customer_reward_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_customer_transaction` (
  `customer_transaction_id` int(11) NOT NULL AUTO_INCREMENT,
  `customer_id` int(11) NOT NULL,
  `order_id` int(11) NOT NULL,
  `description` text NOT NULL,
  `amount` decimal(15,4) NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`customer_transaction_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Планова дата відправки замовлення (admin/model/sale/order.php updateDateShipping, список виробництва).
-- Тип узято з DATE(...) у запиті; старий сайт міг мати '0000-00-00' замість NULL, NewErp читає такі як порожні.
ALTER TABLE `dd_order`
  ADD COLUMN `date_shipping` date NULL;

-- ═══ Каталог: форма товару старої адмінки (admin/model/catalog/product.php) ═══
-- Власні колонки опису товару; у справжній базі вони, ймовірно, NOT NULL без типового значення — звірити.
ALTER TABLE `dd_product_description`
  ADD COLUMN `table_size` text NULL,
  ADD COLUMN `variant` text NULL,
  ADD COLUMN `notice` text NULL,
  ADD COLUMN `model_info` text NULL,
  ADD COLUMN `characteristics` text NULL;

-- Стандартні таблиці OpenCart 3 (install/opencart.sql).
CREATE TABLE `dd_seo_url` (
  `seo_url_id` int(11) NOT NULL AUTO_INCREMENT,
  `store_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `query` varchar(255) NOT NULL,
  `keyword` varchar(255) NOT NULL,
  PRIMARY KEY (`seo_url_id`),
  KEY `query` (`query`),
  KEY `keyword` (`keyword`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_product_related` (
  `product_id` int(11) NOT NULL,
  `related_id` int(11) NOT NULL,
  PRIMARY KEY (`product_id`,`related_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Власні таблиці старого сайту: кольори й варіанти одного виробу (зв'язок в обидва боки) і назва кольору.
-- Колонки взято із запитів editProduct; типи вгадані, звірити.
CREATE TABLE `dd_product_related_color` (
  `product_id` int(11) NOT NULL,
  `related_id` int(11) NOT NULL,
  PRIMARY KEY (`product_id`,`related_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_product_related_variant` (
  `product_id` int(11) NOT NULL,
  `related_id` int(11) NOT NULL,
  PRIMARY KEY (`product_id`,`related_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_product_color` (
  `product_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `name` varchar(255) NOT NULL,
  PRIMARY KEY (`product_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- ═══ Каталог: довідники старої адмінки (admin/model/catalog/category.php, search_preset.php) ═══
-- Власні колонки категорії: банер, картинка таблиці розмірів; в описі — шаблон назви товару, рекламна назва,
-- текст таблиці розмірів. Типи вгадані, звірити.
ALTER TABLE `dd_category`
  ADD COLUMN `banner_id` int(11) NOT NULL DEFAULT '0',
  ADD COLUMN `image_table_size` varchar(255) NULL;

ALTER TABLE `dd_category_description`
  ADD COLUMN `product_name` varchar(255) NULL,
  ADD COLUMN `advertising_product_name` varchar(255) NULL,
  ADD COLUMN `description_table_size` text NULL;

-- Стандартні таблиці OpenCart 3 (install/opencart.sql): дерево категорій (closure table), магазин, фільтри.
CREATE TABLE `dd_category_path` (
  `category_id` int(11) NOT NULL,
  `path_id` int(11) NOT NULL,
  `level` int(11) NOT NULL,
  PRIMARY KEY (`category_id`,`path_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_category_to_store` (
  `category_id` int(11) NOT NULL,
  `store_id` int(11) NOT NULL,
  PRIMARY KEY (`category_id`,`store_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_category_filter` (
  `category_id` int(11) NOT NULL,
  `filter_id` int(11) NOT NULL,
  PRIMARY KEY (`category_id`,`filter_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Власна таблиця: готові пошуки вітрини (назва й адреса). Колонки із запитів моделі, типи вгадані.
CREATE TABLE `dd_search_preset` (
  `preset_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `url` varchar(255) NOT NULL,
  `sort_order` int(11) NOT NULL DEFAULT '0',
  `status` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`preset_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- ═══ Каталог: довідники фільтрів, атрибутів, опцій, виробників і завантажень ═══
-- Стандартні таблиці OpenCart 3 (install/opencart.sql), яких немає в нашому дампі.
CREATE TABLE `dd_option` (
  `option_id` int(11) NOT NULL AUTO_INCREMENT,
  `type` varchar(32) NOT NULL,
  `sort_order` int(3) NOT NULL,
  PRIMARY KEY (`option_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_filter_group` (
  `filter_group_id` int(11) NOT NULL AUTO_INCREMENT,
  `sort_order` int(3) NOT NULL,
  PRIMARY KEY (`filter_group_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_filter_group_description` (
  `filter_group_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `name` varchar(64) NOT NULL,
  PRIMARY KEY (`filter_group_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_attribute` (
  `attribute_id` int(11) NOT NULL AUTO_INCREMENT,
  `attribute_group_id` int(11) NOT NULL,
  `sort_order` int(3) NOT NULL,
  PRIMARY KEY (`attribute_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_attribute_description` (
  `attribute_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `name` varchar(64) NOT NULL,
  PRIMARY KEY (`attribute_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_attribute_group` (
  `attribute_group_id` int(11) NOT NULL AUTO_INCREMENT,
  `sort_order` int(3) NOT NULL,
  PRIMARY KEY (`attribute_group_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_attribute_group_description` (
  `attribute_group_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `name` varchar(64) NOT NULL,
  PRIMARY KEY (`attribute_group_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_product_attribute` (
  `product_id` int(11) NOT NULL,
  `attribute_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `text` text NOT NULL,
  PRIMARY KEY (`product_id`,`attribute_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_option_value` (
  `option_value_id` int(11) NOT NULL AUTO_INCREMENT,
  `option_id` int(11) NOT NULL,
  `image` varchar(255) NOT NULL,
  `sort_order` int(3) NOT NULL,
  PRIMARY KEY (`option_value_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_manufacturer` (
  `manufacturer_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(64) NOT NULL,
  `image` varchar(255) DEFAULT NULL,
  `sort_order` int(3) NOT NULL,
  PRIMARY KEY (`manufacturer_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_manufacturer_to_store` (
  `manufacturer_id` int(11) NOT NULL,
  `store_id` int(11) NOT NULL,
  PRIMARY KEY (`manufacturer_id`,`store_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_download` (
  `download_id` int(11) NOT NULL AUTO_INCREMENT,
  `filename` varchar(160) NOT NULL,
  `mask` varchar(128) NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`download_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_download_description` (
  `download_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `name` varchar(64) NOT NULL,
  PRIMARY KEY (`download_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_product_to_download` (
  `product_id` int(11) NOT NULL,
  `download_id` int(11) NOT NULL,
  PRIMARY KEY (`product_id`,`download_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;


-- ═══ Вміст вітрини: інформаційні сторінки, FAQ, блог, питання покупців, відгуки ═══
-- Стандартні таблиці OpenCart 3 (install/opencart.sql).
CREATE TABLE `dd_information` (
  `information_id` int(11) NOT NULL AUTO_INCREMENT,
  `bottom` int(1) NOT NULL DEFAULT '0',
  `sort_order` int(3) NOT NULL DEFAULT '0',
  `status` tinyint(1) NOT NULL DEFAULT '1',
  PRIMARY KEY (`information_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_information_description` (
  `information_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `title` varchar(64) NOT NULL,
  `description` mediumtext NOT NULL,
  `meta_title` varchar(255) NOT NULL,
  `meta_description` varchar(255) NOT NULL,
  `meta_keyword` varchar(255) NOT NULL,
  PRIMARY KEY (`information_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_information_to_store` (
  `information_id` int(11) NOT NULL,
  `store_id` int(11) NOT NULL,
  PRIMARY KEY (`information_id`,`store_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_review` (
  `review_id` int(11) NOT NULL AUTO_INCREMENT,
  `product_id` int(11) NOT NULL,
  `customer_id` int(11) NOT NULL,
  `author` varchar(64) NOT NULL,
  `text` text NOT NULL,
  `rating` int(1) NOT NULL,
  `status` tinyint(1) NOT NULL DEFAULT '0',
  `date_added` datetime NOT NULL,
  `date_modified` datetime NOT NULL,
  PRIMARY KEY (`review_id`),
  KEY `product_id` (`product_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Власні таблиці старого сайту: FAQ і блог (admin/model/catalog/faq.php, admin/model/blog/*.php).
-- Колонки взято із запитів моделей; типи вгадані, звірити.
CREATE TABLE `dd_faq` (
  `faq_id` int(11) NOT NULL AUTO_INCREMENT,
  `author` varchar(64) NOT NULL DEFAULT '',
  `email` varchar(96) NOT NULL DEFAULT '',
  `comment` text NULL,
  `sort_order` int(11) NOT NULL DEFAULT '0',
  `status` tinyint(1) NOT NULL DEFAULT '1',
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`faq_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_faq_description` (
  `faq_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `name` varchar(255) NOT NULL,
  `description` text NOT NULL,
  PRIMARY KEY (`faq_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_post` (
  `post_id` int(11) NOT NULL AUTO_INCREMENT,
  `image` varchar(255) NOT NULL DEFAULT '',
  `big_image` varchar(255) NOT NULL DEFAULT '',
  `is_top` tinyint(1) NOT NULL DEFAULT '0',
  `status` tinyint(1) NOT NULL DEFAULT '1',
  `date_available` datetime NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`post_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_post_description` (
  `post_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `name` varchar(255) NOT NULL,
  `description` mediumtext NOT NULL,
  `meta_title` varchar(255) NOT NULL DEFAULT '',
  `meta_description` varchar(255) NOT NULL DEFAULT '',
  `image_alt` varchar(255) NOT NULL DEFAULT '',
  PRIMARY KEY (`post_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_post_category` (
  `post_category_id` int(11) NOT NULL AUTO_INCREMENT,
  `status` tinyint(1) NOT NULL DEFAULT '1',
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`post_category_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_post_category_description` (
  `post_category_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `name` varchar(255) NOT NULL,
  PRIMARY KEY (`post_category_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_post_to_category` (
  `post_id` int(11) NOT NULL,
  `post_category_id` int(11) NOT NULL,
  PRIMARY KEY (`post_id`,`post_category_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- Питання покупців у старій адмінці мають ще місто, відповідь (comment), статус, дату й картинку
-- (admin/model/catalog/question.php). Типи вгадані, звірити.
ALTER TABLE `dd_question`
  ADD COLUMN `city` varchar(128) NOT NULL DEFAULT '',
  ADD COLUMN `comment` text NULL,
  ADD COLUMN `status` tinyint(1) NOT NULL DEFAULT '0',
  ADD COLUMN `date_question` date NULL,
  ADD COLUMN `image` varchar(255) NULL;

-- ═══ Покупці: IP, спроби входу, схвалення, групи, власні поля (стандартні таблиці OpenCart 3, install/opencart.sql) ═══
CREATE TABLE `dd_customer_ip` (
  `customer_ip_id` int(11) NOT NULL AUTO_INCREMENT,
  `customer_id` int(11) NOT NULL,
  `ip` varchar(40) NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`customer_ip_id`),
  KEY `ip` (`ip`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_customer_login` (
  `customer_login_id` int(11) NOT NULL AUTO_INCREMENT,
  `email` varchar(96) NOT NULL,
  `ip` varchar(40) NOT NULL,
  `total` int(4) NOT NULL,
  `date_added` datetime NOT NULL,
  `date_modified` datetime NOT NULL,
  PRIMARY KEY (`customer_login_id`),
  KEY `email` (`email`),
  KEY `ip` (`ip`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_customer_approval` (
  `customer_approval_id` int(11) NOT NULL AUTO_INCREMENT,
  `customer_id` int(11) NOT NULL,
  `type` varchar(9) NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`customer_approval_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_customer_group` (
  `customer_group_id` int(11) NOT NULL AUTO_INCREMENT,
  `approval` int(1) NOT NULL,
  `sort_order` int(3) NOT NULL,
  PRIMARY KEY (`customer_group_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_custom_field` (
  `custom_field_id` int(11) NOT NULL AUTO_INCREMENT,
  `type` varchar(32) NOT NULL,
  `value` text NOT NULL,
  `validation` varchar(255) NOT NULL,
  `location` varchar(10) NOT NULL,
  `status` tinyint(1) NOT NULL,
  `sort_order` int(3) NOT NULL,
  PRIMARY KEY (`custom_field_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_custom_field_description` (
  `custom_field_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `name` varchar(128) NOT NULL,
  PRIMARY KEY (`custom_field_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_custom_field_value` (
  `custom_field_value_id` int(11) NOT NULL AUTO_INCREMENT,
  `custom_field_id` int(11) NOT NULL,
  `sort_order` int(3) NOT NULL,
  PRIMARY KEY (`custom_field_value_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_custom_field_value_description` (
  `custom_field_value_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `custom_field_id` int(11) NOT NULL,
  `name` varchar(128) NOT NULL,
  PRIMARY KEY (`custom_field_value_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_custom_field_customer_group` (
  `custom_field_id` int(11) NOT NULL,
  `customer_group_id` int(11) NOT NULL,
  `required` tinyint(1) NOT NULL,
  PRIMARY KEY (`custom_field_id`,`customer_group_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;


-- ═══ Оплата monobank (модуль plata by mono старого сайту) ═══
-- install() створює Id, InvoiceId, OrderId, SecretKey; status, amount, createdDate, modifiedDate модуль дописав пізніше
-- (admin/model/extension/payment/mono.php updatePayment). Типи останніх вгадані: amount тут у гривнях, звірити.
CREATE TABLE `dd_mono_orders` (
  `Id` int NOT NULL AUTO_INCREMENT,
  `InvoiceId` varchar(50) DEFAULT NULL,
  `OrderId` int(10) DEFAULT NULL,
  `SecretKey` varchar(51) DEFAULT NULL,
  `status` varchar(32) DEFAULT NULL,
  `amount` decimal(15,4) NOT NULL DEFAULT '0.0000',
  `createdDate` datetime NULL,
  `modifiedDate` datetime NULL,
  PRIMARY KEY (`Id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- ═══ Маркетинг: коди відстеження кампаній (стандартна таблиця OpenCart 3) ═══
CREATE TABLE `dd_marketing` (
  `marketing_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(32) NOT NULL,
  `description` text NOT NULL,
  `code` varchar(64) NOT NULL,
  `clicks` int(5) NOT NULL DEFAULT '0',
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`marketing_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

-- ═══ Instagram-блок вітрини (admin/model/extension/module/instagram.php addImage) ═══
-- Колонки із запиту; instagram_id — bigint, бо номери дописів Instagram довші за int. Типи вгадані, звірити.
CREATE TABLE `dd_instagram` (
  `instagram_id` bigint(20) NOT NULL,
  `media_type` varchar(32) NOT NULL DEFAULT '',
  `media_url` text NOT NULL,
  `caption` text NOT NULL,
  `thumbnail_url` text NOT NULL,
  `permalink` varchar(255) NOT NULL DEFAULT '',
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`instagram_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ═══ Адміністрування: групи користувачів OpenCart, API, завантажені файли (стандартні таблиці OpenCart 3) ═══
CREATE TABLE `dd_user_group` (
  `user_group_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(64) NOT NULL,
  `permission` text NOT NULL,
  PRIMARY KEY (`user_group_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_api` (
  `api_id` int(11) NOT NULL AUTO_INCREMENT,
  `username` varchar(64) NOT NULL,
  `key` text NOT NULL,
  `status` tinyint(1) NOT NULL,
  `date_added` datetime NOT NULL,
  `date_modified` datetime NOT NULL,
  PRIMARY KEY (`api_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_api_ip` (
  `api_ip_id` int(11) NOT NULL AUTO_INCREMENT,
  `api_id` int(11) NOT NULL,
  `ip` varchar(40) NOT NULL,
  PRIMARY KEY (`api_ip_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_api_session` (
  `api_session_id` int(11) NOT NULL AUTO_INCREMENT,
  `api_id` int(11) NOT NULL,
  `session_id` varchar(32) NOT NULL,
  `ip` varchar(40) NOT NULL,
  `date_added` datetime NOT NULL,
  `date_modified` datetime NOT NULL,
  PRIMARY KEY (`api_session_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_upload` (
  `upload_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `filename` varchar(255) NOT NULL,
  `code` varchar(255) NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`upload_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;


-- ═══ Довідники статусів, банери, звіти (стандартні таблиці OpenCart 3, install/opencart.sql) ═══
CREATE TABLE `dd_stock_status` (
  `stock_status_id` int(11) NOT NULL AUTO_INCREMENT,
  `language_id` int(11) NOT NULL,
  `name` varchar(32) NOT NULL,
  PRIMARY KEY (`stock_status_id`,`language_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_banner` (
  `banner_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(64) NOT NULL,
  `status` tinyint(1) NOT NULL,
  PRIMARY KEY (`banner_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_banner_image` (
  `banner_image_id` int(11) NOT NULL AUTO_INCREMENT,
  `banner_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `title` varchar(64) NOT NULL,
  `link` varchar(255) NOT NULL,
  `image` varchar(255) NOT NULL,
  `sort_order` int(3) NOT NULL DEFAULT '0',
  PRIMARY KEY (`banner_image_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_customer_activity` (
  `customer_activity_id` int(11) NOT NULL AUTO_INCREMENT,
  `customer_id` int(11) NOT NULL,
  `key` varchar(64) NOT NULL,
  `data` text NOT NULL,
  `ip` varchar(40) NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`customer_activity_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_customer_search` (
  `customer_search_id` int(11) NOT NULL AUTO_INCREMENT,
  `store_id` int(11) NOT NULL,
  `language_id` int(11) NOT NULL,
  `customer_id` int(11) NOT NULL,
  `keyword` varchar(255) NOT NULL,
  `category_id` int(11),
  `sub_category` tinyint(1) NOT NULL,
  `description` tinyint(1) NOT NULL,
  `products` int(11) NOT NULL,
  `ip` varchar(40) NOT NULL,
  `date_added` datetime NOT NULL,
  PRIMARY KEY (`customer_search_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

CREATE TABLE `dd_statistics` (
  `statistics_id` int(11) NOT NULL AUTO_INCREMENT,
  `code` varchar(64) NOT NULL,
  `value` decimal(15,4) NOT NULL,
  PRIMARY KEY (`statistics_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 COLLATE=utf8_general_ci;

