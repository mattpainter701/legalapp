# Tabs3 / PracticeMaster ODBC Schema Dump

Schema-only dump parsed from installed vendor ODBC help files. Concrete customer
paths/endpoints are env-owned and must stay in local env files.
No customer rows from `.dat` / `.idx` files were read or exported.

- Generated: `2026-06-17T17:01:26.981015+00:00`
- Source data path: `LEGACY_TABS3_DATABASE_PATH`
- Tabs3 server endpoint: `LEGACY_TABS3_SERVER_ENDPOINT`
- Products: `6`
- ODBC tables/files: `91`
- Documented fields: `2128`

Full machine-readable schema is in [tabs3-odbc-schema.json](tabs3-odbc-schema.json).

## Product Summary

| Product | Tables | Fields |
|-|-:|-:|
| Accounts Payable | 7 | 171 |
| General Ledger | 10 | 137 |
| PracticeMaster | 27 | 568 |
| System Configuration | 2 | 128 |
| Tabs3 Billing | 35 | 763 |
| Trust Accounting | 10 | 361 |

## Table Index

| Product | ODBC Table | Title | Fields | Source |
|-|-|-|-:|-|
| Accounts Payable | `APINVOICEGL` | Invoice File GLS Info (APINVOICEGL) | 6 | `apmain/odbc___file_list___apinvoicegl.htm` |
| Accounts Payable | `APRECURGL` | Recurring Entry GL Info File (APRECURGL) | 5 | `apmain/odbc___file_list___aprecurgl.htm` |
| Accounts Payable | `BANKACCT` | Bank File (BANKACCT) | 25 | `apmain/odbc___file_list___bankacct.htm` |
| Accounts Payable | `CONTACT` | Contact File (CONTACT) | 79 | `apmain/odbc___file_list___contact_file.htm` |
| Accounts Payable | `INVOICE` | Invoice File (INVOICE) | 39 | `apmain/odbc___file_list___invoice.htm` |
| Accounts Payable | `RECURRING` | Recurring Entry File (RECURRING) | 8 | `apmain/odbc___file_list___recurring.htm` |
| Accounts Payable | `VOIDEDCHECKS` | Voided Checks File (VOIDEDCHECKS) | 9 | `apmain/odbc___file_list___voidedchecks.htm` |
| General Ledger | `ACCOUNT` | Chart of Accounts File (ACCOUNT) | 32 | `glmain/odbc___file_list___account.htm` |
| General Ledger | `BALANCE` | Balance File (BALANCE) | 6 | `glmain/odbc___file_list___balance.htm` |
| General Ledger | `BANKIMPORT` | Bank Import File (BANKIMPORT) | 16 | `glmain/odbc___file_list___bankimport.htm` |
| General Ledger | `BUDGET` | Budget File (BUDGET) | 6 | `glmain/odbc___file_list___budget.htm` |
| General Ledger | `DEPARTMENT` | Department File (DEPARTMENT) | 3 | `glmain/odbc___file_list___department.htm` |
| General Ledger | `DEPOSIT` | Deposit Slips File (DEPOSIT) | 9 | `glmain/odbc___file_list___deposit_slips.htm` |
| General Ledger | `JOURNAL` | Journal Entry File (JOURNAL) | 32 | `glmain/odbc___file_list___journal.htm` |
| General Ledger | `JOURNALNAME` | Journal File (JOURNALNAME) | 3 | `glmain/odbc___file_list___journal_name.htm` |
| General Ledger | `RECON` | Reconciliation File (RECON) | 17 | `glmain/odbc___file_list___recon.htm` |
| General Ledger | `RECURRINGENTRY` | Recurring Entry File (RECURRINGENTRY) | 13 | `glmain/odbc___file_list___recurring_entry.htm` |
| PracticeMaster | `CMARC` | PracticeMaster Email Attachments File (CMARC) | 9 | `cmmain/odbc_file_list___cmarc_file.htm` |
| PracticeMaster | `CMAUDIT` | PracticeMaster History Tracking File (CMAUDIT) | 10 | `cmmain/odbc_file_list___history_file.htm` |
| PracticeMaster | `CMBILLTO` | PracticeMaster Bill To File (CMBILLTO) | 16 | `cmmain/odbc_file_list___billto_file.htm` |
| PracticeMaster | `CMCAL` | PracticeMaster Calendar File (CMCAL) | 54 | `cmmain/odbc_file_list___calendar_file.htm` |
| PracticeMaster | `CMCALCOD` | PracticeMaster Calendar Code File (CMCALCOD) | 12 | `cmmain/odbc_file_list___calcode_file.htm` |
| PracticeMaster | `CMCAT` | PracticeMaster Category File (CMCAT) | 4 | `cmmain/odbc_file_list___category_file.htm` |
| PracticeMaster | `CMCHKREQ` | PracticeMaster Check Request File (CMCHKREQ) | 21 | `cmmain/odbc_file_list___chkreq_file.htm` |
| PracticeMaster | `CMCLIENT` | PracticeMaster Client File (CMCLIENT) | 88 | `cmmain/odbc_file_list___client_file.htm` |
| PracticeMaster | `CMCOST` | PracticeMaster Cost File (CMCOST) | 28 | `cmmain/odbc_file_list___cost_file.htm` |
| PracticeMaster | `CMDOCIMP` | PracticeMaster Document Import File (CMDOCIMP) | 10 | `cmmain/odbc_file_list___docimp_file.htm` |
| PracticeMaster | `CMDOCMGT` | PracticeMaster Document Management File (CMDOCMGT) | 33 | `cmmain/odbc_file_list___docmgmt_file.htm` |
| PracticeMaster | `CMDOCTYP` | PracticeMaster Document Type File (CMDOCTYP) | 3 | `cmmain/odbc_file_list___doctyp_file.htm` |
| PracticeMaster | `CMDOCVSN` | PracticeMaster Document Version File (CMDOCVSN) | 8 | `cmmain/odbc_file_list___docvsn_file.htm` |
| PracticeMaster | `CMEMPL` | PracticeMaster Timekeeper File (CMEMPL) | 6 | `cmmain/odbc_file_list___timekeeper_file.htm` |
| PracticeMaster | `CMENOTE` | PracticeMaster eNote File (CMENOTE) | 22 | `cmmain/odbc_file_list___enote_file.htm` |
| PracticeMaster | `CMFEE` | PracticeMaster Fee File (CMFEE) | 29 | `cmmain/odbc_file_list___fee_file.htm` |
| PracticeMaster | `CMJRNL` | PracticeMaster Journal File (CMJRNL) | 32 | `cmmain/odbc_file_list___journal_file.htm` |
| PracticeMaster | `CMLOC` | PracticeMaster Location File (CMLOC) | 3 | `cmmain/odbc_file_list___location_file.htm` |
| PracticeMaster | `CMMACRO` | PracticeMaster Text Macro File (CMMACRO) | 3 | `cmmain/odbc_file_list___macro_file.htm` |
| PracticeMaster | `CMOLLOG` | PracticeMaster Outlook Log File (CMOLLOG) | 22 | `cmmain/odbc_file_list___outlook_log_file.htm` |
| PracticeMaster | `CMRELATE` | PracticeMaster Contact File (CMRELATE) | 108 | `cmmain/odbc_file_list___contact_file.htm` |
| PracticeMaster | `CMRELLNK` | PracticeMaster Contact Category File (CMRELLNK) | 3 | `cmmain/odbc_file_list___rellnk_file.htm` |
| PracticeMaster | `CMSECCAS` | PracticeMaster Secure Client File (CMSECCAS) | 3 | `cmmain/odbc_file_list___seccas_file.htm` |
| PracticeMaster | `CMTBCODE` | PracticeMaster Task Code File (CMTBCODE) | 4 | `cmmain/odbc_file_list___taskcode_file.htm` |
| PracticeMaster | `CMTCODE` | PracticeMaster Transaction Code File (CMTCODE) | 13 | `cmmain/odbc_file_list___tcode_file.htm` |
| PracticeMaster | `CMWKFLOW` | PracticeMaster WorkFlow File (CMWKFLOW) | 17 | `cmmain/odbc_file_list___wkflow_file.htm` |
| PracticeMaster | `CMXREF` | PracticeMaster Cross Reference File (CMXREF) | 7 | `cmmain/odbc_file_list___xref_file.htm` |
| System Configuration | `CONTACT` | Contact File (CONTACT) | 79 | `scmain/odbc_file_list___contact_file.htm` |
| System Configuration | `USER` | User Configuration File (USER) | 49 | `scmain/odbc_file_list___user.htm` |
| Tabs3 Billing | `APCOSTLINK` | AP Cost Integration File (APCOSTLINK) | 7 | `tbmain/odbc_file_list___apcostlink_file.htm` |
| Tabs3 Billing | `ARCHIVE` | Archive File (ARCHIVE) | 54 | `tbmain/odbc_file_list___archive_file.htm` |
| Tabs3 Billing | `BILLFREQ` | Billing Frequency File (BILLFREQ) | 4 | `tbmain/odbc_file_list___billing_frequency_file.htm` |
| Tabs3 Billing | `BILLTO` | Bill To File (BILLTO) | 22 | `tbmain/odbc_file_list___billto_file.htm` |
| Tabs3 Billing | `BUDGET` | Budget File (BUDGET) | 11 | `tbmain/odbc_file_list___budget_file.htm` |
| Tabs3 Billing | `CATEGORY` | Category File (CATEGORY) | 4 | `tbmain/odbc_file_list___category_file.htm` |
| Tabs3 Billing | `CLIENT` | Client File (CLIENT) | 140 | `tbmain/odbc_file_list___client_file.htm` |
| Tabs3 Billing | `CLIENTCUSTOM` | Custom Fields File (CLIENTCUSTOM) | 6 | `tbmain/odbc_file_list___custom_fields_file.htm` |
| Tabs3 Billing | `CLIENTNOTE` | Client Notes File (CLIENTNOTE) | 4 | `tbmain/odbc_file_list___client_notes_file.htm` |
| Tabs3 Billing | `CLIENTPORTAL` | Client Portal File (CLIENTPORTAL) | 4 | `tbmain/odbc_file_list___client_portal_file.htm` |
| Tabs3 Billing | `CLIENTRATE` | Client Rate File (CLIENTRATE) | 9 | `tbmain/odbc_file_list___client_rate_file.htm` |
| Tabs3 Billing | `CONTACT` | Contact File (CONTACT) | 86 | `tbmain/odbc_file_list___contact_file.htm` |
| Tabs3 Billing | `COST` | Cost File (COST) | 30 | `tbmain/odbc_file_list___cost_file.htm` |
| Tabs3 Billing | `COSTRATE` | Cost Rate File (COSTRATE) | 11 | `tbmain/odbc_file_list___cost_rate_file.htm` |
| Tabs3 Billing | `EMAILTEMPLATE` | Email Template File (EMAILTEMPLATE) | 10 | `tbmain/odbc_file_list___email_template_file.htm` |
| Tabs3 Billing | `EMPLOYEE` | Employee File (EMPLOYEE) | 32 | `tbmain/odbc_file_list___employee_file.htm` |
| Tabs3 Billing | `EMPLRULE` | Fee Compensation Rules File (EMPLRULE) | 13 | `tbmain/odbc_file_list___fee_compensation_rules_file.htm` |
| Tabs3 Billing | `FEE` | Fee File (FEE) | 28 | `tbmain/odbc_file_list___fee_file.htm` |
| Tabs3 Billing | `FUND` | Client Funds File (FUND) | 18 | `tbmain/odbc_file_list___client_funds_file.htm` |
| Tabs3 Billing | `GLACCTMAP` | GLS Integration Setup File (GLACCTMAP) | 10 | `tbmain/odbc_file_list___gls_integration_setup_file.htm` |
| Tabs3 Billing | `LEDGALLOC` | Client Ledger Allocation File (LEDGALLOC) | 11 | `tbmain/odbc_file_list___client_ledger_allocation_file.htm` |
| Tabs3 Billing | `LEDGER` | Client Ledger File (LEDGER) | 65 | `tbmain/odbc_file_list___client_ledger_file.htm` |
| Tabs3 Billing | `LOCATION` | Location File (LOCATION) | 3 | `tbmain/odbc_file_list___location_file.htm` |
| Tabs3 Billing | `MACRO` | Macro File (MACRO) | 3 | `tbmain/odbc_file_list___macro_file.htm` |
| Tabs3 Billing | `PAYMENT` | Payment File (PAYMENT) | 25 | `tbmain/odbc_file_list___payment_file.htm` |
| Tabs3 Billing | `SECCLT` | Secure Client File (SECCLT) | 3 | `tbmain/odbc_file_list___secure_client_file.htm` |
| Tabs3 Billing | `SPLITBILLING` | Split Billing File (SPLITBILLING) | 13 | `tbmain/odbc_file_list___split_fee_file.htm` |
| Tabs3 Billing | `STMTCODE` | Statement Template File (STMTCODE) | 39 | `tbmain/odbc_file_list___statement_template_file.htm` |
| Tabs3 Billing | `STMTDET` | Statement Detail File (STMTDET) | 9 | `tbmain/odbc_file_list___statement_detail_file.htm` |
| Tabs3 Billing | `STMTDETALLOC` | Statement Allocation File (STMTDETALLOC) | 13 | `tbmain/odbc_file_list___statement_allocation_file.htm` |
| Tabs3 Billing | `STMTTRAK` | Statement Tracking File (STMTTRAK) | 29 | `tbmain/odbc_file_list___pre-bill_tracking_file.htm` |
| Tabs3 Billing | `TASKBILLCODE` | Task Code File (TASKBILLCODE) | 5 | `tbmain/odbc_file_list___task_code_file.htm` |
| Tabs3 Billing | `TASKBUDGET` | Task Code Budget File (TASKBUDGET) | 5 | `tbmain/odbc_file_list___task_code_budget_file.htm` |
| Tabs3 Billing | `TCODE` | Tcode (TCODE) | 17 | `tbmain/odbc_file_list___tcode_file.htm` |
| Tabs3 Billing | `TRUSTREQUEST` | Trust Request File (TRUSTREQUEST) | 20 | `tbmain/odbc_file_list___trust_request_file.htm` |
| Trust Accounting | `ATTORNEY` | Attorney File (ATTORNEY) | 32 | `trmain/odbc_file_list___attorney_file.htm` |
| Trust Accounting | `BANK` | Bank File (BANK) | 44 | `trmain/odbc_file_list___bank_file.htm` |
| Trust Accounting | `BANKIMPORT` | Bank Import File (BANKIMPORT) | 16 | `trmain/odbc_file_list___bankimport.htm` |
| Trust Accounting | `CLIENT` | Client File (CLIENT) | 135 | `trmain/odbc_file_list___client_file.htm` |
| Trust Accounting | `COMBINEDTRANS` | Combined Transaction File (COMBINEDTRANS) | 11 | `trmain/odbc_file_list___combinedtrans.htm` |
| Trust Accounting | `CONTACT` | Contact File (CONTACT) | 79 | `trmain/odbc_file_list___contact_file.htm` |
| Trust Accounting | `FININST` | Financial Institution Account File (FININST) | 9 | `trmain/odbc_file_list___fininst.htm` |
| Trust Accounting | `PAYEE` | Payee File (PAYEE) | 7 | `trmain/odbc_file_list___payee_file.htm` |
| Trust Accounting | `RECON` | Reconciliation File (RECON) | 19 | `trmain/odbc_file_list___recon.htm` |
| Trust Accounting | `VOIDCHECK` | Void Check (VOIDCHECK) | 9 | `trmain/odbc_file_list___void_check.htm` |

## Key Tables For Migration Planning

### Accounts Payable: `BANKACCT`

- Title: Bank File (BANKACCT)
- Field count: 25
- Source: `apmain/odbc___file_list___bankacct.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `BANK` | Bank Number |  |
| `DESC` | Bank Description | maximum of 30 characters |
| `LAST_CHECK` | Last Check Number | 0-99999999 |
| `GL_CASH_ACCT` | GLS Cash Account Number |  |
| `GL_AP_ACCT` | GLS Accounts Payable Account Number |  |
| `USE_L1572` | Use Nelco form L1572 | 0=unchecked, 1=checked |
| `AUTO_SWITCH_L1572` | Switch from Nelco form L1445 to L1572 | 0=unchecked, 1=checked |
| `FINAL_L1445_CHECK` | Last Nelco form L1445 check number |  |
| `FIRST_L1572_CHECK` | First Nelco form L1572 check number |  |
| `INACTIVE` | Inactive | 0=unchecked, 1=checked |
| `POSPAY_INCPAYEE` | * |  |
| `POSPAY_INCACCONO` | * |  |
| `POSPAY_INCCOLHDR` | * |  |
| `POSPAY_INCVOID` | * |  |
| `POSPAY_VOIDIND` | * |  |
| `POSPAY_LASTEXPUSR` | * |  |
| `POSPAY_EXPDATE` | * |  |
| `POSPAY_EXPTIME` | * |  |
| `POSPAY_CUTOFF` | * |  |
| `POSPAY_WELLSFARGO` | * |  |
| `POSPAY_INVOICENUM` | * |  |
| `POSPAY_INVOICEDESC` | * |  |
| `POSPAY_ROUTINGNUM` | * |  |
| `RESERVED` | * |  |

### Accounts Payable: `CONTACT`

- Title: Contact File (CONTACT)
- Field count: 79
- Source: `apmain/odbc___file_list___contact_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `RP_Key` | Contact Key |  |
| `Name` | Full Name |  |
| `Alt_Name` | * |  |
| `Calc_Name_Sw` | Update Full Name based on First, Middle, and Last Name fields | 0=Cleared 1=Selected |
| `First_Name` | First Name |  |
| `Middle_Name` | Middle Name |  |
| `Last_Name` | Last Name |  |
| `Initials` | Initials |  |
| `Organization` | Organization Name |  |
| `Org_Sw` | Organization Switch | 0=Individual 1=Organization |
| `Inactive` | Inactive | 0=Active 1=Inactive |
| `Addr_No` | Default Address Selector | 1=Home 2=Business 3=Other |
| `Phone1_Src` | Phone Number Selector (top-left) | Assistant_Phone Callback Car_Phone Cellular_Phone Company_Phone Home_Fax Home_Phone Home_Phone2 ISDN Other_Fax Other_Phone Pager Primary_Phone Radio_Phone Telex TTY_TDD_Phone Work_Fax Work_Phone Work_Phone2 |
| `Phone2_Src` | Phone Number Selector (bottom-left) | Same types as PHONE1_SRC |
| `Phone3_Src` | Phone Number Selector (top-right) | Same types as PHONE1_SRC |
| `Phone4_Src` | Phone Number Selector (bottom-right) | Same types as PHONE1_SRC |
| `Addr1_Line1` | Business Address Line 1 |  |
| `Addr1_Line2` | Business Address Line 2 |  |
| `Addr1_Line3` | Business Address Line 3 |  |
| `Addr1_City` | Business Address City |  |
| `Addr1_State` | Business Address State |  |
| `Addr1_Zip` | Business Address Zip |  |
| `Addr1_Country` | Business Address Country |  |
| `Addr2_Line1` | Home Address Line 1 |  |
| `Addr2_Line2` | Home Address Line 2 |  |
| `Addr2_Line3` | Home Address Line 3 |  |
| `Addr2_City` | Home Address City |  |
| `Addr2_State` | Home Address State |  |
| `Addr2_Zip` | Home Address Zip |  |
| `Addr2_Country` | Home Address Country |  |
| `Addr3_Line1` | Other Address Line 1 |  |
| `Addr3_Line2` | Other Address Line 2 |  |
| `Addr3_Line3` | Other Address Line 3 |  |
| `Addr3_City` | Other Address City |  |
| `Addr3_State` | Other Address State |  |
| `Addr3_Zip` | Other Address Zip |  |
| `Addr3_Country` | Other Address Country |  |
| `Assistant_Phone` | Assistant Phone Number |  |
| `Work_Phone` | Business Phone Number |  |
| `Work_Phone2` | Business 2 Phone Number |  |
| `Work_Fax` | Business Fax Number |  |
| `Callback` | Callback Phone Number |  |
| `Car_Phone` | Car Phone Number |  |
| `Company_Phone` | Company Phone Number |  |
| `Home_Phone` | Home Phone Number |  |
| `Home_Phone2` | Home 2 Phone Number |  |
| `Home_Fax` | Home Fax Number |  |
| `ISDN` | ISDN Number |  |
| `Cellular_Phone` | Mobile Phone Number |  |
| `Other_Phone` | Other Phone Number |  |
| `Other_Fax` | Other Fax Number |  |
| `Pager` | Pager Number |  |
| `Primary_Phone` | Primary Phone Number |  |
| `Radio_Phone` | Radio Phone Number |  |
| `Telex` | Telex Number |  |
| `TTY_TDD_Phone` | TTY/TDD Phone Number |  |
| `Addr1` | Default Address Line 1 | Virtual field linking to the currently specified default address. |
| `Addr2` | Default Address Line 2 | Same as Addr1 |
| `Addr3` | Default Address Line 3 | Same as Addr1 |
| `City` | Default City | Same as Addr1 |
| `State` | Default State | Same as Addr1 |
| `Zip` | Default Zip | Same as Addr1 |
| `Country` | Default Country | Same as Addr1 |
| `Phone1` | Default Phone 1 | Same as Addr1 |
| `Phone2` | Default Phone 2 | Same as Addr1 |
| `Phone3` | Default Phone 3 | Same as Addr1 |
| `Phone4` | Default Phone 4 | Same as Addr1 |
| `PM_Integration` | * |  |
| `IsClientSw` | * |  |
| `IsVendorSw` | * |  |
| `IsPayeeSw` | * |  |
| `IsUserSw` | * |  |
| `Email_Address1` | Default Email Address 1 | Same as Addr1 |
| `Email_Address2` | Default Email Address 2 | Same as Addr1 |
| `Email_Address3` | Default Email Address 3 | Same as Addr1 |
| `Web_Page` | Default Web Page | Same as Addr1 |
| `Comments` |  |  |
| `LP_CONTACT_ID` | LawPay Contact ID |  |

### Accounts Payable: `INVOICE`

- Title: Invoice File (INVOICE)
- Field count: 39
- Source: `apmain/odbc___file_list___invoice.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `VENDOR` | Vendor Number |  |
| `PAYEE` | Vendor Name |  |
| `REF_NUM` | Reference Number |  |
| `VOUCHER` | Voucher Number |  |
| `INVOICE` | Invoice Number |  |
| `INV_DATE` | Invoice Date |  |
| `DUE_DATE` | Due Date |  |
| `INV_AMT` | Invoice Amount |  |
| `DISC_AMT` | Discount Amount |  |
| `DISC_DATE` | Discount Date |  |
| `TAKE_DISC` | Take Discount Indicator | 0=unchecked, 1=checked |
| `HOLD` | On Hold Indicator | Hold, Pay (H,P) |
| `BANK` | Bank Number |  |
| `CHECK_NUM` | Check Number |  |
| `AMT_PAID` | Net Amount |  |
| `DATE_PAID` | Date Paid |  |
| `AUTO_MANUAL` | Transaction Type | A=Unpaid Invoice (Auto), M=Manual Check, E=EFT |
| `UNPAID_POSTED` | Posted Unpaid indicator |  |
| `UNPAID_POST_DATE` | Date Posted Unpaid |  |
| `AMT_TO_PAY` | Amount to Pay |  |
| `ORIG_AMT` | Original Invoice Amount |  |
| `MISC_SEQNO` | Internal Invoice Sequence Number |  |
| `USER_ID` | User ID that created invoice |  |
| `CREATE_DATE` | Date invoice was created |  |
| `CREATE_TIME` | Time invoice was created |  |
| `DESC` | Invoice description |  |
| `MEMO` | Check Memo |  |
| `EXCLUDE_FROM_1099` | Exclude from 1099 indicator | 0=unchecked, 1=checked |
| `POSPAY_EXPORTED` | Indicates whether the transaction has been exported via Positive Pay | 0=No, 1=Yes |
| `MISC_CONTACT` | Name | Contact ID in Address Details |
| `ALT_ADDR1` | Address Line 1 | Address 1 in Address Details (stored for paid invoice) |
| `ALT_ADDR2` | Address Line 2 | Same as ALT_ADDR1 |
| `ALT_ADDR3` | Address Line 3 | Same as ALT_ADDR1 |
| `ALT_CITY` | City | Same as ALT_ADDR1 |
| `ALT_STATE` | State | Same as ALT_ADDR1 |
| `ALT_ZIP` | Zip Code | Same as ALT_ADDR1 |
| `ALT_COUNTRY` | Country | Same as ALT_ADDR1 |
| `ATTACHMENT` | The path to the attachment file, if one exists. |  |

### General Ledger: `JOURNAL`

- Title: Journal Entry File (JOURNAL)
- Field count: 32
- Source: `glmain/odbc___file_list___journal.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `TRANS_NUM` | Transaction Number |  |
| `FLAGS` | * |  |
| `ACCT` | Account Number |  |
| `JRNL` | Journal Number | 1-30 |
| `DATE` | Date of Journal Entry |  |
| `REF` | Reference |  |
| `CHECK_NUM` | Check Number | maximum 20 characters |
| `DBCR` | Debit/Credit | D=Debit, C=Credit |
| `TRANS_TYPE` | Status | C=Cleared, D=Deposited, R=Reconciled, U=Unapplied Payment, or blank |
| `RECEIPT_TYPE` | Receipt Type | -1=None, 0=Cash, 1=Check, 2=Credit Card, 3=Other, 4=Client Funds, 5=EFT. 6=Payment, 7=Chargeback, 8=Purchase, 9=Balance Transfer, 10=Cash Advance, 11=Fee, 12=Interest |
| `AMT` | Amount |  |
| `DEPT` | Department Number | 1-99 |
| `SOURCE` | Source of Journal Entry | Manual, Billing, Trust, AP, Recurring Entry, or Payroll (M, B, T, A, R, P) |
| `RECON` | Reconciliation indicator | N=Outstanding, C=Cleared, R=Reconciled |
| `DESC` | Description | maximum 120 characters |
| `T3_FILE` | Tabs3 File | P=Payment file, C=Cost file, L=Ledger adjustment, or blank |
| `T3_SEQNO` | * |  |
| `T3_AMT` | Tabs3 Entry Amount |  |
| `BATCH` | Batch Number |  |
| `USER_ID` | User ID that created entry |  |
| `CREATE_DATE` | Date Journal entry was created |  |
| `CREATE_TIME` | Time Journal entry was created |  |
| `REVERSED` | Reversed entry | 0=not reversed, 1=reversed |
| `TRANSFER` | Account Transfer type | C=Credit, D=Debit, N=False |
| `DEPOSIT_ID` | * |  |
| `PENDING_SEQNO` | * |  |
| `BANKTR_SEQNO` | * |  |
| `RECON_SEQNO` | * |  |
| `RECON_SKIP` | * |  |
| `MAN_RECON_SEQNO` | * |  |
| `T3_COMBINE_ID` | * |  |

### General Ledger: `JOURNALNAME`

- Title: Journal File (JOURNALNAME)
- Field count: 3
- Source: `glmain/odbc___file_list___journal_name.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `JRNL` | Journal Number | 1-30 |
| `DESC` | Journal Description | maximum 30 characters |

### PracticeMaster: `CMAUDIT`

- Title: PracticeMaster History Tracking File (CMAUDIT)
- Field count: 10
- Source: `cmmain/odbc_file_list___history_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `AOD_IP` | * |  |
| `File_ID` | File |  |
| `File_Seq_No` | * |  |
| `User_ID` | User ID |  |
| `Date` | Change Date |  |
| `Time` | Change Time |  |
| `Audit_Type` | Audit Type | ACD |
| `Field_Name` | Field Name |  |
| `Old_Value` | Old Value |  |

### PracticeMaster: `CMCAL`

- Title: PracticeMaster Calendar File (CMCAL)
- Field count: 54
- Source: `cmmain/odbc_file_list___calendar_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `Due_Date` | Due Date |  |
| `Complet_Dt` | Date Completed |  |
| `Calendar_Code` | Calendar Code |  |
| `Desc` | Description |  |
| `Type` | Type |  |
| `Start_Time` | Time Start |  |
| `End_Time` | Time End |  |
| `Client_ID` | Client ID |  |
| `Related_Party` | Contact |  |
| `User_ID` | User ID |  |
| `Priority` | Priority |  |
| `Private` | Private | 0=Not Private 1=Private |
| `AlarmExpr` | Alarm Time |  |
| `Reminder1` | Reminder 1 |  |
| `Reminder2` | Reminder 2 |  |
| `Reminder3` | Reminder 3 |  |
| `LinkNo` | * |  |
| `Pri_Key_Date_SeqNo` | * |  |
| `Sec_Key_Date_SeqNo` | * |  |
| `Root_SeqNo` | * |  |
| `Orig_Template_SeqNo` | * |  |
| `Fee_SeqNo` | * |  |
| `Status` | Status |  |
| `CLActive` | * |  |
| `SnoozeDate` | Snooze Date |  |
| `SnoozeTime` | Snooze Time |  |
| `SnoozeVerification` | Snooze Verification |  |
| `CR_System_ID` | * |  |
| `CR_Jurisdiction_Id` | * |  |
| `CR_TriggerItem_Id` | * |  |
| `End_Date` | End Date |  |
| `Due_Date_UTC` | Due Date UTC |  |
| `Start_Time_UTC` | Start Time UTC |  |
| `End_Date_UTC` | End Date UTC |  |
| `End_Time_UTC` | End Time UTC |  |
| `SnoozeDate_UTC` | Snooze Date UTC |  |
| `SnoozeTime_UTC` | Snooze Time UTC |  |
| `No_Timezone` | No TimeZone. This option determines whether the UTC fields are used to determine the Due Date, Start Time, and End Time for a given user. | 0=Time Zone Selected 1=No Time Zone Selected |
| `GroupInfo` | User/Group |  |
| `Location` | Location |  |
| `Comments` | Comments |  |
| `Rule` | Rule |  |
| `IntegID` |  |  |
| `CLMisc` | * |  |
| `PlanVars` | * |  |
| `CR_Misc` | * |  |
| `TimeZone` | * |  |
| `IntegID_Sort` | * |  |
| `IntegInfo` | * |  |
| `Required_Attendees` | Required Attendees synchronized from Outlook | Requires Microsoft 365 Exchange Connector |
| `Optional_Attendees` | Optional Attendees synchronized from Outlook | Requires Microsoft 365 Exchange Connector |
| `Doc1` | Document1 |  |
| `Doc2` | Document2 |  |

### PracticeMaster: `CMCLIENT`

- Title: PracticeMaster Client File (CMCLIENT)
- Field count: 88
- Source: `cmmain/odbc_file_list___client_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `Client_ID` | Client ID |  |
| `Name` | Client Name |  |
| `Contact` | Contact Name |  |
| `Alpha_Search` | Name Search |  |
| `Client_Full_Name` | Client Full Name |  |
| `Contact_Full_Name` | Contact Full Name |  |
| `Addr_No` | Address Selector | 1=Business 2=Home 3=Other |
| `Email_Addr_No` | Email Address Selector | 1-3 |
| `Addr1` | Address Line 1 | Virtual field from Contact File |
| `Addr2` | Address Line 2 | Same as Addr1 |
| `Addr3` | Address Line 3 | Same as Addr1 |
| `City` | City | Same as Addr1 |
| `State` | State | Same as Addr1 |
| `Zip` | Zip Code | Same as Addr1 |
| `Country` | Country | Same as Addr1 |
| `Phone1` | Matter Contact Phone Number (top-left) | Same as Addr1 |
| `Phone2` | Matter Contact Phone Number (bottom-left) | Same as Addr1 |
| `Phone3` | Matter Contact Phone Number (top-right) | Same as Addr1 |
| `Phone4` | Matter Contact Phone Number (bottom-right) | Same as Addr1 |
| `Phone1_Src` | Phone Number Selector (top-left) | Assistant_Phone Callback Car_Phone Cellular_Phone Company_Phone Home_Fax Home_Phone Home_Phone2 ISDN Other_Fax Other_Phone Pager Primary_Phone Radio_Phone Telex TTY_TDD_Phone Work_Fax Work_Phone Work_Phone2 |
| `Phone2_Src` | Phone Number Selector (bottom-left) | Same as Phone1_Src |
| `Phone3_Src` | Phone Number Selector (top-right) | Same as Phone1_Src |
| `Phone4_Src` | Phone Number Selector (bottom-right) | Same as Phone1_Src |
| `Phone` | Matter Contact Business Phone Number |  |
| `Fax_Phone` | Matter Contact Fax Phone Number |  |
| `Home_Phone` | Matter Contact Home Phone Number |  |
| `Cellular_Phone` | Matter Contact Mobile Phone Number |  |
| `Alt_Addr1` | Address Line 1 | Secure/Matter Mode |
| `Alt_Addr2` | Address Line 2 | Secure/Matter Mode |
| `Alt_Addr3` | Address Line 3 | Secure/Matter Mode |
| `Alt_City` | City | Secure/Matter Mode |
| `Alt_State` | State | Secure/Matter Mode |
| `Alt_Zip` | Zip | Secure/Matter Mode |
| `Alt_Country` | Country | Secure/Matter Mode |
| `Alt_Work_Phone` | Business Phone Number | Secure/Matter Mode |
| `Alt_Work_Fax` | Fax Phone Number | Secure/Matter Mode |
| `Alt_Home_Phone` | Home Phone Number | Secure/Matter Mode |
| `Alt_Cellular_Phone` | Cellular Phone Number | Secure/Matter Mode |
| `Location` | Location |  |
| `Desc` | Work Description |  |
| `Task_Based_Billing` | Task Based Billing Switch | 0=unchecked, 1=checked |
| `Inactive` | Inactive Switch | 0=unchecked, 1=checked |
| `Secure_Client` | Secure Client Switch | 0=unchecked, 1=checked |
| `Misc_1` | Miscellaneous Line 1 |  |
| `Misc_2` | Miscellaneous Line 2 |  |
| `Misc_3` | Miscellaneous Line 3 |  |
| `Date_Open` | Open Date |  |
| `Close_Date` | Close Date |  |
| `Prim_Tkpr` | Primary Timekeeper # |  |
| `Sec_Tkpr` | Secondary Timekeeper # |  |
| `Orig_Tkpr` | Originating Timekeeper # |  |
| `Category` | Category | 0-999 |
| `T3_Integration` | * |  |
| `QB_Integration` | * |  |
| `QBEditSeq` | * |  |
| `QBListID` | * |  |
| `AOP` | Area of Practice |  |
| `Referred_By` | Referred By |  |
| `Ref_No` | File Reference Number |  |
| `Stat_Limit` | Statute of Limitations |  |
| `Opp_Atty` | Opposing Attorney |  |
| `Fil_County` | County of Filing |  |
| `State_Jurs` | State of Jurisdiction |  |
| `Conty_Jurs` | County of Jurisdiction |  |
| `Court_Jurs` | Court of Jurisdiction |  |
| `Judge` | Judge |  |
| `Verdict` | Verdict or Outcome |  |
| `Agree_Date` | Date of Fee Agreement |  |
| `Agree_Type` | Type of Fee Agreement |  |
| `CR_Jurisdiction_Id` | * |  |
| `Doc_Import_Id` | * |  |
| `CRM_Lead_Id` | * |  |
| `Style` | Style (If Starter Data is installed, the field name is Case Style) | If Starter Data is installed, this is a memo field, otherwise it is an Alpha field |
| `Email_Address` | Email Address | Same as Addr1 |
| `Web_Page` | Web Page | Same as Addr1 |
| `Alt_Email_Address` | Alt Email Address | Secure/Matter Mode |
| `Alt_Web_Page` | Alt Web Page | Secure/Matter Mode |
| `Client_Photo` | Client Photo |  |
| `Comments` | Comments | Same as Addr1 |
| `Tax_ID` | Tax ID | If Starter Data is installed, this is a virtual field from the Contact File and will not appear in ODBC, otherwise it is an Alpha field |
| `Reserved` | * |  |
| `Closed_File_Loc` | Closed File Location |  |
| `Sched_Destroy_Date` | Scheduled Destroy Date |  |
| `Signing_Atty` | Signing Attorney |  |
| `Date_Destroyed` | Date Destroyed |  |
| `Closed_File_Notes` | Closed File Notes |  |
| `Court_Notes` | Court Notes |  |

### PracticeMaster: `CMCOST`

- Title: PracticeMaster Cost File (CMCOST)
- Field count: 28
- Source: `cmmain/odbc_file_list___cost_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `Client_ID` | Client ID |  |
| `Timekeeper` | Timekeeper Number |  |
| `Date` | Date |  |
| `Tcode` | Tcode |  |
| `Phase_Task` | Phase/Task |  |
| `Activity` | Expense ID |  |
| `Bill_Code` | Bill Code |  |
| `Hold` | Status |  |
| `Amount` | Amount |  |
| `Archived` | Archived Transaction | 0=Work in Process 1=Archived |
| `Stmt_Date` | Statement Date |  |
| `Stmt_Num` | Statement Number |  |
| `Units` | Units |  |
| `Rate` | Rate |  |
| `Mirror_Seq_No` | * |  |
| `QBEditSeq` | * |  |
| `QBTxnId` | * |  |
| `QBVendor` | * |  |
| `QBAcct` | * |  |
| `User_ID` | User ID |  |
| `Create_Date` | Creation Date |  |
| `Create_Time` | Creation Time |  |
| `Rate_Code` | Rate Code |  |
| `Sales_Tax` | Sales Tax |  |
| `PendingAmtSw` | * |  |
| `PendingRateSw` | * |  |
| `Description` | Description |  |

### PracticeMaster: `CMEMPL`

- Title: PracticeMaster Timekeeper File (CMEMPL)
- Field count: 6
- Source: `cmmain/odbc_file_list___timekeeper_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `EMPL` | Timekeeper Number |  |
| `NAME` | Timekeeper Name |  |
| `INITIALS` | Initials |  |
| `INACTIVE` | * |  |
| `RATE1` | Rate 1 |  |

### PracticeMaster: `CMFEE`

- Title: PracticeMaster Fee File (CMFEE)
- Field count: 29
- Source: `cmmain/odbc_file_list___fee_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `Client_ID` | Client ID |  |
| `Timekeeper` | Timekeeper Number |  |
| `Category` | Category |  |
| `Date` | Date |  |
| `Tcode` | Tcode |  |
| `Phase_Task` | Phase/Task |  |
| `Activity` | Activity |  |
| `Bill_Code` | Bill Code |  |
| `Hold` | Status |  |
| `Rate` | Rate |  |
| `Hours` | Hours to Bill |  |
| `Amount` | Amount |  |
| `Archived` | Archived Transaction | 0=Work in Process 1=Archived |
| `Stmt_Date` | Statement Date |  |
| `Stmt_Num` | Statement Number |  |
| `Worked_Hours` | Hours Worked |  |
| `Mirror_Seq_No` | * |  |
| `QBEditSeq` | * |  |
| `QBTxnId` | * |  |
| `User_ID` | User ID |  |
| `Create_Date` | Creation Date |  |
| `Create_Time` | Creation Time |  |
| `Rate_Code` | Rate Code |  |
| `Sales_Tax` | Sales Tax |  |
| `PendingRateSw` | * |  |
| `PendingAmtSw` | * |  |
| `Location` | Geolocation |  |
| `Description` | Description |  |

### PracticeMaster: `CMJRNL`

- Title: PracticeMaster Journal File (CMJRNL)
- Field count: 32
- Source: `cmmain/odbc_file_list___journal_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `Record_Type` | Record Type | N=Note E=Email P=Phone Task T=Time Task R=Research Task B=Billing Note |
| `Date` | Date |  |
| `Time` | Time |  |
| `Duration` | Duration |  |
| `Status` | Status |  |
| `Client_ID` | Client ID |  |
| `Related_Party` | Contact |  |
| `User_ID` | User ID |  |
| `Subject` | Subject |  |
| `Spoke_With` | Spoke With | 0=Cleared 1=Selected |
| `Returned_Call` | Returned Call | 0=Cleared 1=Selected |
| `Left_Message` | Left Message | 0=Cleared 1=Selected |
| `Voice_Message` | Voice Message | 0=Cleared 1=Selected |
| `User_Updating` | User Updating |  |
| `First_Date` | First Date |  |
| `Last_Date` | Last Date |  |
| `First_Time` | First Time |  |
| `Last_Time` | Last Time |  |
| `Fee_SeqNo` | * |  |
| `Phone_No` | Phone Number |  |
| `Email_To` | To |  |
| `Email_From` | From |  |
| `Email_CC` | CC |  |
| `Email_BCC` | BCC |  |
| `Desc` | Description |  |
| `Date_Time` | * |  |
| `Research_URL` | Research URL |  |
| `Research_File` | Research File |  |
| `Email_Body` | Email Body |  |
| `Email_Attachments` | Email Attachments |  |
| `Orig_Email` | Original Email |  |

### PracticeMaster: `CMRELATE`

- Title: PracticeMaster Contact File (CMRELATE)
- Field count: 108
- Source: `cmmain/odbc_file_list___contact_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `RP_Key` | Contact ID |  |
| `Name` | Full Name |  |
| `Organization` | Organization Name |  |
| `Org_Sw` | Organization Switch | 0=Individual 1=Organization |
| `Inactive` | Inactive | 0=Active 1=Inactive |
| `County` | County |  |
| `Addr1` | Default Address Line 1 | Virtual field linking to the currently specified default address. |
| `Addr2` | Default Address Line 2 | Same as Addr1 |
| `Addr3` | Default Address Line 3 | Same as Addr1 |
| `City` | Default City | Same as Addr1 |
| `State` | Default State | Same as Addr1 |
| `Zip` | Default Zip | Same as Addr1 |
| `Country` | Default Country | Same as Addr1 |
| `Phone1` | Phone Number Displayed (top-left) | Virtual field linking to the phone record currently displayed. |
| `Phone2` | Phone Number Displayed (bottom-left) | Same as Phone 1 |
| `Phone3` | Phone Number Displayed (top-right) | Same as Phone 1 |
| `Phone4` | Phone Number Displayed (bottom-right) | Same as Phone 1 |
| `Addr_No` | Default Address Selector | 1=Home 2=Business 3=Other |
| `Addr1_Line1` | Business Address Line 1 |  |
| `Addr1_Line2` | Business Address Line 2 |  |
| `Addr1_Line3` | Business Address Line 3 |  |
| `Addr1_City` | Business Address City |  |
| `Addr1_State` | Business Address State |  |
| `Addr1_Zip` | Business Address Zip |  |
| `Addr1_Country` | Business Address Country |  |
| `Addr2_Line1` | Home Address Line 1 |  |
| `Addr2_Line2` | Home Address Line 2 |  |
| `Addr2_Line3` | Home Address Line 3 |  |
| `Addr2_City` | Home Address City |  |
| `Addr2_State` | Home Address State |  |
| `Addr2_Zip` | Home Address Zip |  |
| `Addr2_Country` | Home Address Country |  |
| `Addr3_Line1` | Other Address Line 1 |  |
| `Addr3_Line2` | Other Address Line 2 |  |
| `Addr3_Line3` | Other Address Line 3 |  |
| `Addr3_City` | Other Address City |  |
| `Addr3_State` | Other Address State |  |
| `Addr3_Zip` | Other Address Zip |  |
| `Addr3_Country` | Other Address Country |  |
| `Phone1_Src` | Phone Number Selector (top-left) | Assistant_Phone Callback Car_Phone Cellular_Phone Company_Phone Home_Fax Home_Phone Home_Phone2 ISDN Other_Fax Other_Phone Pager Primary_Phone Radio_Phone Telex TTY_TDD_Phone Work_Fax Work_Phone Work_Phone2 |
| `Phone2_Src` | Phone Number Selector (bottom-left) | Same types as PHONE1_SRC |
| `Phone3_Src` | Phone Number Selector (top-right) | Same types as PHONE1_SRC |
| `Phone4_Src` | Phone Number Selector (bottom-right) | Same types as PHONE1_SRC |
| `Assistant_Phone` | Assistant Phone |  |
| `Work_Phone` | Business Phone |  |
| `Work_Phone2` | Business Phone 2 |  |
| `Work_Fax` | Business Fax |  |
| `Callback` | Callback |  |
| `Car_Phone` | Car Phone |  |
| `Company_Phone` | Company Phone |  |
| `Home_Phone` | Home Phone |  |
| `Home_Phone2` | Home Phone 2 |  |
| `Home_Fax` | Home Fax |  |
| `ISDN` | ISDN |  |
| `Cellular_Phone` | Mobile Phone |  |
| `Other_Phone` | Other Phone |  |
| `Other_Fax` | Other Fax |  |
| `Pager` | Pager |  |
| `Primary_Phone` | Primary Phone |  |
| `Radio_Phone` | Radio Phone |  |
| `Telex` | Telex Number |  |
| `TTY_TDD_Phone` | TTY/TDD Phone |  |
| `Do_Not_Sync` | Do Not Sync | 0=Sync 1=Do Not Sync |
| `T3_Integration` | * |  |
| `Salutation` | Salutation |  |
| `First_Name` | First Name |  |
| `Last_Name` | Last Name |  |
| `Contact_1` | Contact Name |  |
| `Contact_2` | Secondary Contact Name |  |
| `First_Date` | First Contact Date |  |
| `Last_Date` | Last Contact Date |  |
| `Reg_No` | Registration Number |  |
| `Specialty` | Specialty |  |
| `Background` | General Background |  |
| `DOB` | Date of Birth |  |
| `Gender` | Gender |  |
| `RP_Cat` | Contact Category |  |
| `Email_Address` | Email Address | Virtual field linking to the email currently displayed. |
| `Web_Page` | Web Page |  |
| `Email_Address1` | Email 1 Address |  |
| `Email_Address2` | Email 2 Address |  |
| `Email_Address3` | Email 3 Address |  |
| `RP_Photo` | Contact Photo |  |
| `Comments` | Comments |  |
| `GroupInfo` | Group Info |  |
| `IntegID` | * |  |
| `Social_Media1_Label` | Other 1 Label |  |
| `Social_Media2_Label` | Other 2 Label |  |
| `Birthday_Card` | Birthday Card | 0=Cleared 1=Selected |
| `Holiday_Card` | Holiday Card | 0=Cleared 1=Selected |
| `Holiday_Gift` | Holiday Gift | 0=Cleared 1=Selected |
| `Holiday_Party` | Holiday Party | 0=Cleared 1=Selected |
| `Newsletter` | Newsletter | 0=Cleared 1=Selected |
| `Event_Tickets` | Event Tickets | 0=Cleared 1=Selected |
| `DOD` | Date of Death |  |
| `Marital_Status` | Marital Status |  |
| `Spouse_Name` | Spouse’s Name |  |
| `Driv_Lic_No` | Driver License |  |
| `Driver_Lic_State` | Driver License State |  |
| `Last_Used` | Date Last Used |  |
| `Use_Again` | Use Again? |  |
| `Facebook` | Facebook |  |
| `Twitter` | Twitter |  |
| `LinkedIn` | LinkedIn |  |
| `Social_Media1` | Other 1 |  |
| `Social_Media2` | Other 2 |  |
| `Marketing_Notes` | Marketing Notes |  |

### PracticeMaster: `CMRELLNK`

- Title: PracticeMaster Contact Category File (CMRELLNK)
- Field count: 3
- Source: `cmmain/odbc_file_list___rellnk_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `RP_Cat` | Category |  |
| `RP_Key` | Contact ID |  |

### PracticeMaster: `CMXREF`

- Title: PracticeMaster Cross Reference File (CMXREF)
- Field count: 7
- Source: `cmmain/odbc_file_list___xref_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `RP_Seq_No` | * |  |
| `Client_Id` | Client ID |  |
| `Aop_Id` | Area of Practice |  |
| `File_Id` | File Name |  |
| `Field_No` | * |  |
| `File_Seq_No` | * |  |

### System Configuration: `CONTACT`

- Title: Contact File (CONTACT)
- Field count: 79
- Source: `scmain/odbc_file_list___contact_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `RP_Key` | Contact Key |  |
| `Name` | Full Name |  |
| `Alt_Name` | * |  |
| `Calc_Name_Sw` | Update Full Name based on First, Middle, and Last Name fields | 0=Cleared 1=Selected |
| `First_Name` | First Name |  |
| `Middle_Name` | Middle Name |  |
| `Last_Name` | Last Name |  |
| `Initials` | Initials |  |
| `Organization` | Organization Name |  |
| `Org_Sw` | Organization Switch | 0=Individual 1=Organization |
| `Inactive` | Inactive | 0=Active 1=Inactive |
| `Addr_No` | Default Address Selector | 1=Home 2=Business 3=Other |
| `Phone1_Src` | Phone Number Selector (top-left) | Assistant_Phone Callback Car_Phone Cellular_Phone Company_Phone Home_Fax Home_Phone Home_Phone2 ISDN Other_Fax Other_Phone Pager Primary_Phone Radio_Phone Telex TTY_TDD_Phone Work_Fax Work_Phone Work_Phone2 |
| `Phone2_Src` | Phone Number Selector (bottom-left) | Same types as PHONE1_SRC |
| `Phone3_Src` | Phone Number Selector (top-right) | Same types as PHONE1_SRC |
| `Phone4_Src` | Phone Number Selector (bottom-right) | Same types as PHONE1_SRC |
| `Addr1_Line1` | Business Address Line 1 |  |
| `Addr1_Line2` | Business Address Line 2 |  |
| `Addr1_Line3` | Business Address Line 3 |  |
| `Addr1_City` | Business Address City |  |
| `Addr1_State` | Business Address State |  |
| `Addr1_Zip` | Business Address Zip |  |
| `Addr1_Country` | Business Address Country |  |
| `Addr2_Line1` | Home Address Line 1 |  |
| `Addr2_Line2` | Home Address Line 2 |  |
| `Addr2_Line3` | Home Address Line 3 |  |
| `Addr2_City` | Home Address City |  |
| `Addr2_State` | Home Address State |  |
| `Addr2_Zip` | Home Address Zip |  |
| `Addr2_Country` | Home Address Country |  |
| `Addr3_Line1` | Other Address Line 1 |  |
| `Addr3_Line2` | Other Address Line 2 |  |
| `Addr3_Line3` | Other Address Line 3 |  |
| `Addr3_City` | Other Address City |  |
| `Addr3_State` | Other Address State |  |
| `Addr3_Zip` | Other Address Zip |  |
| `Addr3_Country` | Other Address Country |  |
| `Assistant_Phone` | Assistant Phone Number |  |
| `Work_Phone` | Business Phone Number |  |
| `Work_Phone2` | Business 2 Phone Number |  |
| `Work_Fax` | Business Fax Number |  |
| `Callback` | Callback Phone Number |  |
| `Car_Phone` | Car Phone Number |  |
| `Company_Phone` | Company Phone Number |  |
| `Home_Phone` | Home Phone Number |  |
| `Home_Phone2` | Home 2 Phone Number |  |
| `Home_Fax` | Home Fax Number |  |
| `ISDN` | ISDN Number |  |
| `Cellular_Phone` | Mobile Phone Number |  |
| `Other_Phone` | Other Phone Number |  |
| `Other_Fax` | Other Fax Number |  |
| `Pager` | Pager Number |  |
| `Primary_Phone` | Primary Phone Number |  |
| `Radio_Phone` | Radio Phone Number |  |
| `Telex` | Telex Number |  |
| `TTY_TDD_Phone` | TTY/TDD Phone Number |  |
| `Addr1` | Default Address Line 1 | Virtual field linking to the currently specified default address. |
| `Addr2` | Default Address Line 2 | Same as Addr1 |
| `Addr3` | Default Address Line 3 | Same as Addr1 |
| `City` | Default City | Same as Addr1 |
| `State` | Default State | Same as Addr1 |
| `Zip` | Default Zip | Same as Addr1 |
| `Country` | Default Country | Same as Addr1 |
| `Phone1` | Phone Number Displayed (top-left) | Virtual field linking to the phone record currently displayed. |
| `Phone2` | Phone Number Displayed (bottom-left) | Same as Phone 1 |
| `Phone3` | Phone Number Displayed (top-right) | Same as Phone 1 |
| `Phone4` | Phone Number Displayed (bottom-right) | Same as Phone 1 |
| `PM_Integration` | * |  |
| `IsClientSw` | * |  |
| `IsVendorSw` | * |  |
| `IsPayeeSw` | * |  |
| `IsUserSw` | * |  |
| `Email_Address1` | Email 1 Address |  |
| `Email_Address2` | Email 2 Address |  |
| `Email_Address3` | Email 3 Address |  |
| `Web_Page` | Web Page URL |  |
| `Comments` | Comments |  |
| `LP_CONTACT_ID` | LawPay Contact ID |  |

### System Configuration: `USER`

- Title: User Configuration File (USER)
- Field count: 49
- Source: `scmain/odbc_file_list___user.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `User_ID` | User ID |  |
| `User_Name` | User Full Name | Virtual field from the Contact file |
| `User_Contact_ID` | User Name | Linked Contact ID |
| `Group_Name` | Group Name |  |
| `User_Init` | Initials | Virtual field from the Contact file |
| `Password` | * |  |
| `PWHash` | * |  |
| `Verif_ID` | * |  |
| `Access1` | * |  |
| `Access2` | * |  |
| `Access3` | * |  |
| `Access4` | * |  |
| `Access5` | * |  |
| `User_Timekeepers` | Assign Tabs3/PracticeMaster Timekeepers | 1 = Selected, 0 = Cleared |
| `Msg_Count` | * |  |
| `Group_Sw` | Group Switch | 1 = Group, 0 = User |
| `Login_User_Sw` | Login User | 1 = Selected, 0 = Cleared |
| `Txt_Color` | Text Color |  |
| `Bk_Color` | Background Color |  |
| `Phone_Src` | Phone Number Selector (top) | 1=Home 2=Business 3=Other |
| `Phone2_Src` | Phone Number Selector (bottom) | 1=Home 2=Business 3=Other |
| `Email_Addr_No` | Email Address Selector | 1-3 |
| `Phone` | Phone Number Displayed (top) | Virtual field from the Contact file |
| `Phone2` | Phone Number Displayed (bottom) | Virtual field from the Contact file |
| `Alt_User_Init` | Initials |  |
| `Alt_Phone` | Phone Number Displayed (top) | Phone 1 for non-login user |
| `Alt_Phone2` | Phone Number Displayed (bottom) | Phone 2 for non-login user |
| `CompuLaw_Sw` | * |  |
| `Inactive_User_Sw` | Inactive | 1 = Selected, 0 = Cleared |
| `Alpha_1` | * |  |
| `Alpha_2` | * |  |
| `Alpha_3` | * |  |
| `LongInt_1` | * |  |
| `LongInt_2` | * |  |
| `LongInt_3` | * |  |
| `Boolean_1` | * |  |
| `Boolean_2` | * |  |
| `Boolean_3` | * |  |
| `Timekeepers` | Assigned Timekeeper | Pipe delimited list of timekeeper numbers |
| `Email_Address` | Email Address | Virtual field from the Contact file |
| `Alt_Email_Address` | Alt Email Address | Email address for non-login user |
| `Preferences` | * |  |
| `Registry` | * |  |
| `User_ID_List` | Users in the Group | Pipe delimited list of User IDs in the group |
| `Integration_Prefs` | * |  |
| `Memo_1` | * |  |
| `Memo_2` | * |  |
| `Memo_3` | * |  |

### Tabs3 Billing: `APCOSTLINK`

- Title: AP Cost Integration File (APCOSTLINK)
- Field count: 7
- Source: `tbmain/odbc_file_list___apcostlink_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `DATE` | Date |  |
| `AMOUNT` | Amount |  |
| `INV_SEQNO` | Internal Invoice Sequence Number |  |
| `T3_FILE` | * |  |
| `T3_SEQNO` | Internal Tabs3 Sequence Number |  |

### Tabs3 Billing: `ARCHIVE`

- Title: Archive File (ARCHIVE)
- Field count: 54
- Source: `tbmain/odbc_file_list___archive_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `REC_TYPE` | Record Type | F=Fee C=Cost P=Payment U=Fund |
| `REF_NUM` | Reference Number |  |
| `TIMEKEEPER` | Timekeeper Number |  |
| `DATE` | Date of Transaction |  |
| `BILL_CODE` | Bill Code | 0-4 |
| `RATE_CODE` | Rate Code | 0-9 |
| `TCODE` | Tcode | 0-999 |
| `TCODE_TYPE` | Tcode Type | 0-9 |
| `AMOUNT` | Amount |  |
| `HOLD` | Status | H=Hold P=Print U=Ready to be Updated S=Save |
| `SALES_TAX` | Sales Tax Code | 0-9 |
| `SOURCE` | Source of Transaction | Fees, Costs, Payments B=Billing R=Remote D=Data Capture Device C or P=PracticeMaster or Tabs3 Connect A=Accounts Payable T=Trust Client Funds Transactions M=Manual Payment W=Client Funds Withdrawal |
| `PHASE_TASK` | Phase/Task Code for Task Code Billing | AA001 through ZZ999 |
| `ACTIVITY` | Activity or Expense Code | A001-A999 E001-E999 X001-X999 |
| `EXPENSE_ADVANCE` | Expense/Advance Indicator | E=Expense A=Advance |
| `APPLYTO_STMT_NUM` | Statement Number payment was applied to |  |
| `APPLYTO_STMT_ID` | Sequence Number of statement payment was applied to |  |
| `UNITS` | Units |  |
| `RATE` | Rate |  |
| `ORIGINAL_AMT` | Amount (used to determine write up and write down information) |  |
| `HOURS` | Hours |  |
| `CATEGORY_COST_TYPE` | Category or Cost Type | 0-999 for fees or 0-9 for cost |
| `STATEMENT_NUMBER` | Statement Number on which transaction was billed |  |
| `STATEMENT_ID` | Sequence Number of Statement on which transaction was billed |  |
| `STATEMENT_DATE` | Statement Date |  |
| `COURTESY_DISCOUNT` | Courtesy Discount Amount |  |
| `WIP_SEQNO` | * |  |
| `PYMT_LINK` | * |  |
| `WORKED_HOURS` | Worked Hours |  |
| `QBEditSeq` | * |  |
| `QBTxnID` | * |  |
| `QBVendor` | QuickBooks Vendor |  |
| `QBAcct` | QuickBooks Debit Account |  |
| `USER_ID` | User ID |  |
| `CREATE_DATE` | Date Entered |  |
| `CREATE_TIME` | Time transaction was created |  |
| `GL_CHECK_NUM` | Check Number |  |
| `GL_REF` | * |  |
| `GL_RECEIPT_TYPE` | Receipt Type | 0=Cash, 1=Check, 2=Credit Card, 3=Other Type, 4=Client Funds, 5=EFT |
| `TR_SEQNO` | Sequence Number of Trust transaction (if transaction originated in Trust) |  |
| `FP_LINK` | Client Funds Payment Sequence Number |  |
| `ORIG_TRANS_SEQ_NO` | * |  |
| `LP_TRANSACTION_ID` | LawPay Transaction ID |  |
| `COMBINE_ID` | * |  |
| `RESERVED_LONG_1` | * |  |
| `LOCATION` | Geolocation |  |
| `RESERVED_ALPHA_1` | * |  |
| `DESCRIPTION` | Description | Maximum 5000 characters |
| `CC_TRANS_ID` | PayFuse Transaction ID |  |
| `PP_PAYMENT_ID` | LexCharge Payment ID |  |
| `RESERVED_MEMO_1` | * |  |
| `RESERVED_MEMO_2` | * |  |

### Tabs3 Billing: `BILLTO`

- Title: Bill To File (BILLTO)
- Field count: 22
- Source: `tbmain/odbc_file_list___billto_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `Client_ID` | Client ID |  |
| `Record_No` | * |  |
| `Contact` | Contact |  |
| `Description` | Description |  |
| `Print_Sw` | Print Statement Switch | 0=not selected, 1=selected |
| `Email_Sw` | Email Statement Switch | 0=not selected, 1=selected |
| `Addr_No` | Address Selector | 1=Business 2=Home 3=Other |
| `Email_Addr_No` | Email Address Selector | 1-3 |
| `Email_Template` | Email Template |  |
| `Attn_Sw` | Attention Line Switch | 1=Include 2=Exclude |
| `Attn_Type` | Attention Type Switch | 1=Contact Name 2=Other Name |
| `Attn_Override` | Other Name |  |
| `Contact_Lookup_Sw` | * |  |
| `Alt_Addr1` | Address Line 1 | Secure/Matter Mode only |
| `Alt_Addr2` | Address Line 2 | Secure/Matter Mode only |
| `Alt_Addr3` | Address Line 3 | Secure/Matter Mode only |
| `Alt_City` | Address City | Secure/Matter Mode only |
| `Alt_State` | Address State | Secure/Matter Mode only |
| `Alt_Zip` | Address Zip | Secure/Matter Mode only |
| `Alt_Country` | Address Country | Secure/Matter Mode only |
| `Alt_Email_Address` | Email Address | Secure/Matter Mode only |

### Tabs3 Billing: `CLIENT`

- Title: Client File (CLIENT)
- Field count: 140
- Source: `tbmain/odbc_file_list___client_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `NAME` | Client Name |  |
| `CONTACT` | Contact Name |  |
| `ALPHA_SEARCH` | Name Search |  |
| `Client_Full_Name` | Client Full Name |  |
| `Contact_Full_Name` | Contact Full Name |  |
| `Addr_No` | Address Selector | 1=Business 2=Home 3=Other |
| `Email_Addr_No` | Email Address Selector | 1-3 |
| `ADDR1` | Address Line 1 | Virtual field from Contact File |
| `ADDR2` | Address Line 2 | Virtual field from Contact File |
| `ADDR3` | Address Line 3 | Virtual field from Contact File |
| `CITY` | City | Virtual field from Contact File |
| `STATE` | State | Virtual field from Contact File |
| `ZIP` | Zip Code | Virtual field from Contact File |
| `COUNTRY` | Country | Virtual field from Contact File |
| `Phone1_Src` | Phone Number Selector (top-left) | Assistant_Phone Callback Car_Phone Cellular_Phone Company_Phone Home_Fax Home_Phone Home_Phone2 ISDN Other_Fax Other_Phone Pager Primary_Phone Radio_Phone Telex TTY_TDD_Phone Work_Fax Work_Phone Work_Phone2 |
| `Phone2_Src` | Phone Number Selector (bottom-left) | Same as PHONE1_SRC |
| `Phone3_Src` | Phone Number Selector (top-right) | Same as PHONE1_SRC |
| `Phone4_Src` | Phone Number Selector (bottom-right) | Same as PHONE1_SRC |
| `Phone1` | Matter Contact Phone Number (top-left) |  |
| `Phone2` | Matter Contact Phone Number (bottom-left) |  |
| `Phone3` | Matter Contact Phone Number (top-right) |  |
| `Phone4` | Matter Contact Phone Number (bottom-right) |  |
| `Phone` | Matter Contact Business Phone Number |  |
| `Fax_Phone` | Matter Contact Fax Phone Number |  |
| `Home_Phone` | Matter Contact Home Phone Number |  |
| `Cellular_Phone` | Matter Contact Mobile Phone Number |  |
| `Alt_Addr1` | Address Line 1 | Secure/Matter Mode |
| `Alt_Addr2` | Address Line 2 | Secure/Matter Mode |
| `Alt_Addr3` | Address Line 3 | Secure/Matter Mode |
| `Alt_City` | City | Secure/Matter Mode |
| `Alt_State` | State | Secure/Matter Mode |
| `Alt_Zip` | Zip | Secure/Matter Mode |
| `Alt_Country` | Country | Secure/Matter Mode |
| `Alt_Work_Phone` | Business Phone Number | Secure/Matter Mode |
| `ALT_WORK_FAX` | Fax Phone Number | Secure/Matter Mode |
| `ALT_HOME_PHONE` | Home Phone Number | Secure/Matter Mode |
| `ALT_CELLULAR_PHONE` | Cellular Phone Number | Secure/Matter Mode |
| `DESC` | Work Description |  |
| `MISC_1` | Miscellaneous Line 1 |  |
| `MISC_2` | Miscellaneous Line 2 |  |
| `MISC_3` | Miscellaneous Line 3 |  |
| `DATE_OPEN` | Open Date |  |
| `CLOSE_DATE` | Close Date |  |
| `BILLING_RATE_CODE` | Billing Rate Code |  |
| `HOURLY_RATE` | Hourly Rate |  |
| `PRIM_TKPR` | Primary Timekeeper # |  |
| `SEC_TKPR` | Secondary Timekeeper # |  |
| `ORIG_TKPR` | Originating Timekeeper # |  |
| `CATEGORY` | Category | 0-999 |
| `Client_Portal_Sw` | * |  |
| `CRM_Lead_Id` | * |  |
| `LEVEL_TYPE_CODE` | Timekeeper Level Type | R=Rate C=Code N=None |
| `LEVEL_HOURLY_RATE1` | Level Hourly Rate 1 (when LEVEL_TYPE_CODE = R) |  |
| `LEVEL_HOURLY_RATE2` | Level Hourly Rate 2 |  |
| `LEVEL_HOURLY_RATE3` | Level Hourly Rate 3 |  |
| `LEVEL_HOURLY_RATE4` | Level Hourly Rate 4 |  |
| `LEVEL_HOURLY_RATE5` | Level Hourly Rate 5 |  |
| `LEVEL_HOURLY_RATE6` | Level Hourly Rate 6 |  |
| `LEVEL_HOURLY_RATE7` | Level Hourly Rate 7 |  |
| `LEVEL_HOURLY_RATE8` | Level Hourly Rate 8 |  |
| `LEVEL_HOURLY_RATE9` | Level Hourly Rate 9 |  |
| `LEVEL_CODE1` | Level Code 1 (when LEVEL_TYPE_CODE = C) | 0-9 |
| `LEVEL_CODE2` | Level Code 2 | 0-9 |
| `LEVEL_CODE3` | Level Code 3 | 0-9 |
| `LEVEL_CODE4` | Level Code 4 | 0-9 |
| `LEVEL_CODE5` | Level Code 5 | 0-9 |
| `LEVEL_CODE6` | Level Code 6 | 0-9 |
| `LEVEL_CODE7` | Level Code 7 | 0-9 |
| `LEVEL_CODE8` | Level Code 8 | 0-9 |
| `LEVEL_CODE9` | Level Code 9 | 0-9 |
| `LOCATION` | Location |  |
| `BUDGET_HOURS` | Budget Hours |  |
| `BUDGET_AMOUNT` | Budget Amount |  |
| `DISCOUNT_TYPE` | Discount Type | P=Percentage A=Amount |
| `DISCOUNT_AMOUNT` | Discount Amount |  |
| `DISCOUNT_PERCENT` | Percentage Amount |  |
| `RESET_COURTESY_DISC` | Reset Courtesy Discount Switch | 0=unchecked, 1=checked |
| `TASK_BASED_BILLING` | Task Based Billing Switch | 0=unchecked, 1=checked |
| `INACTIVE` | Inactive Switch | 0=unchecked, 1=checked |
| `BILL_ON_DEMAND` | Bill On Demand Switch | 0=unchecked, 1=checked |
| `RELEASE_TO_BILL` | Release To Bill Switch | 0=unchecked, 1=checked |
| `PROGRESS_BILLING` | Progress Billing Switch | 0=unchecked, 1=checked |
| `BILLING_FREQUENCY` | Billing Frequency |  |
| `NONBILLABLE` | Non-Billable Switch | 0=unchecked, 1=checked |
| `BILL_TO_CODE` | Bill To Code | Y=Yes N=No D=Duplicate |
| `FINANCE_CHARGE` | Finance Charge Switch | 0=unchecked, 1=checked |
| `FINANCE_CHARGE_DAYS` | Finance Charge Days | 0-999 |
| `FINANCE_CHARGE_RATE` | Finance Charge Rate Code | 1-5 |
| `FEE_SALES_TAX_CODE` | Fee Sales Tax Code | 0-9 |
| `EXP_SALES_TAX_CODE` | Expense Sales Tax Code | 0-9 |
| `ADV_SALES_TAX_CODE` | Advance Sales Tax Code | 0-9 |
| `PYMT_ALLOC_METHOD` | Method to Apply Payments | 1-5 |
| `FINCHG_ALLOC_METHOD` | Apply Payments to Finance Charge | F,L |
| `RA_BY_INVOICE` | Receipt Allocation by Invoice Switch | 0=unchecked, 1=checked |
| `COVER_STATMENT` | Cover Statement Option | D=Detail Cover Statement S=Summary Cover Statement N=No Cover Statement I=Individual Detail Cover Statement J=Individual Summary Cover Statement |
| `COMBINE_MATTERS` | Combine Matters | 0=unchecked, 1=checked |
| `TRUST_INTEGRATION` | Trust Integration | D=Detail S=Summary N=None |
| `DRAFT_STMT_CODES` | Draft Statement Template |  |
| `FINAL_STMT_CODES` | Final Statement Template |  |
| `PASSWORD_PROTECT_PDF` | Password Protect PDF Statements Switch | 0=unchecked, 1=checked |
| `PDF_PASSWORD` | Password |  |
| `FEE_REFERENCE_NUM` | * |  |
| `COST_REFERENCE_NUM` | * |  |
| `PYMT_REFERENCE_NUM` | * |  |
| `LEDG_REFERENCE_NUM` | * |  |
| `FUND_REFERENCE_NUM` | * |  |
| `STATEMENT_NUM` | Last Statement Number |  |
| `FUND_BALANCE` | Client Funds Balance |  |
| `REPLENISH_BELOW` | Replenish Below Amount |  |
| `REPLENISH_TO` | Replenish To Amount |  |
| `FUND_APPLICATION` | Fund Application for Client Funds | M=Manual F=Automatic Fee Pymt E=Automatic Expense Pymt A=Automatic Advance Pymt L=Automatic All |
| `ONE_TIME_FUND_BILL` | One Time Retainer Switch | 0=unchecked, 1=checked |
| `FUND_STMT_FORMAT` | Client Funds Statement Format | D=Detail S=Summary N=None |
| `SECURE_CLIENT` | Secure Client Switch | 0=unchecked, 1=checked |
| `MAIN_COVER_CLIENT` | Main Cover Statement Client Switch | 0=unchecked, 1=checked |
| `FEE_THRESHOLD` | Fee Threshold Amount |  |
| `EXP_THRESHOLD` | Expense Threshold Amount |  |
| `ADV_THRESHOLD` | Advance Threshold Amount |  |
| `THRESHOLD_BILLING` | Threshold Billing Items | I=Individual A=All T=Total |
| `TOTAL_THRESHOLD` | Total Threshold Amount |  |
| `RESET_BEG_NOTES` | Reset Beginning Statement Notes Switch | 0=unchecked, 1=checked |
| `RESET_END_NOTES` | Reset Ending Statement Notes Switch | 0=unchecked, 1=checked |
| `BUDGET_WARNING` | Budget Warning Switch | 0=unchecked, 1=checked |
| `QBEditSeq` | * |  |
| `QBListID` | * |  |
| `QB_INTEGRATION` | QuickBooks Integration Switch | 0=unchecked, 1=checked |
| `PM_INTEGRATION` | * |  |
| `SPLIT_FEE_CALC_AMT` | * |  |
| `SPLIT_FEE_CREDITS` | * |  |
| `SPLIT_FEE_ZERO_AMTS` | * |  |
| `SPLIT_COST_CALC_AMT` | * |  |
| `SPLIT_COST_CREDITS` | * |  |
| `SPLIT_COST_ZERO_AMTS` | * |  |
| `EMAIL_ADDRESS` | Email Address |  |
| `Web_Page` | Web Page |  |
| `Alt_Email_Address` | Email Address | Secure/Matter Mode |
| `Alt_Web_Page` | Web Page | Secure/Matter Mode |
| `Reserved` | * |  |

### Tabs3 Billing: `CLIENTCUSTOM`

- Title: Custom Fields File (CLIENTCUSTOM)
- Field count: 6
- Source: `tbmain/odbc_file_list___custom_fields_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `LINE_NUMBER` | Custom Field Line Number | 1-50 |
| `LABEL` | Custom Field Label | maximum 20 characters |
| `TEXT` | Custom Field Text | maximum 255 characters |
| `FLD_TYPE` | * |  |

### Tabs3 Billing: `CLIENTNOTE`

- Title: Client Notes File (CLIENTNOTE)
- Field count: 4
- Source: `tbmain/odbc_file_list___client_notes_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `RECORD_TYPE` | Record Type | B=Beginning Statement Note E=Ending Statement Note I=Billing Notes & Instructions N=Client Notes |
| `DESC` | Note Text |  |

### Tabs3 Billing: `CLIENTPORTAL`

- Title: Client Portal File (CLIENTPORTAL)
- Field count: 4
- Source: `tbmain/odbc_file_list___client_portal_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_Sequence_No` | Internal Sequence Number |  |
| `Client_Id` | Client ID |  |
| `Contact` | Contact ID |  |
| `Permissions` | * |  |

### Tabs3 Billing: `CLIENTRATE`

- Title: Client Rate File (CLIENTRATE)
- Field count: 9
- Source: `tbmain/odbc_file_list___client_rate_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `SHARED_SWITCH` | Shared Switch | 0 = unchecked, 1 = checked |
| `SHARED_CLIENT_ID` | Shared Client ID |  |
| `FIRST_RECORD` | First Record for each Client ID | 0 = false, 1 = true |
| `TIMEKEEPER` | Timekeeper Number |  |
| `RATE` | Rate |  |
| `NEW_RATE` | New Rate |  |
| `FEE_DATE` | Effective Date of New Rate |  |

### Tabs3 Billing: `CONTACT`

- Title: Contact File (CONTACT)
- Field count: 86
- Source: `tbmain/odbc_file_list___contact_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `RP_Key` | Contact Key |  |
| `Name` | Full Name |  |
| `Alt_Name` | * |  |
| `Calc_Name_Sw` | Update Full Name based on First, Middle, and Last Name fields | 0=Cleared 1=Selected |
| `First_Name` | First Name |  |
| `Middle_Name` | Middle Name |  |
| `Last_Name` | Last Name |  |
| `Initials` | Initials |  |
| `Organization` | Organization Name |  |
| `Org_Sw` | Organization Switch | 0=Individual 1=Organization |
| `Inactive` | Inactive | 0=Active 1=Inactive |
| `Addr_No` | Default Address Selector | 1=Home 2=Business 3=Other |
| `Phone1_Src` | Phone Number Selector (top-left) | Assistant_Phone Callback Car_Phone Cellular_Phone Company_Phone Home_Fax Home_Phone Home_Phone2 ISDN Other_Fax Other_Phone Pager Primary_Phone Radio_Phone Telex TTY_TDD_Phone Work_Fax Work_Phone Work_Phone2 |
| `Phone2_Src` | Phone Number Selector (bottom-left) | Same types as PHONE1_SRC |
| `Phone3_Src` | Phone Number Selector (top-right) | Same types as PHONE1_SRC |
| `Phone4_Src` | Phone Number Selector (bottom-right) | Same types as PHONE1_SRC |
| `Addr1_Line1` | Business Address Line 1 |  |
| `Addr1_Line2` | Business Address Line 2 |  |
| `Addr1_Line3` | Business Address Line 3 |  |
| `Addr1_City` | Business Address City |  |
| `Addr1_State` | Business Address State |  |
| `Addr1_Zip` | Business Address Zip |  |
| `Addr1_Country` | Business Address Country |  |
| `Addr2_Line1` | Home Address Line 1 |  |
| `Addr2_Line2` | Home Address Line 2 |  |
| `Addr2_Line3` | Home Address Line 3 |  |
| `Addr2_City` | Home Address City |  |
| `Addr2_State` | Home Address State |  |
| `Addr2_Zip` | Home Address Zip |  |
| `Addr2_Country` | Home Address Country |  |
| `Addr3_Line1` | Other Address Line 1 |  |
| `Addr3_Line2` | Other Address Line 2 |  |
| `Addr3_Line3` | Other Address Line 3 |  |
| `Addr3_City` | Other Address City |  |
| `Addr3_State` | Other Address State |  |
| `Addr3_Zip` | Other Address Zip |  |
| `Addr3_Country` | Other Address Country |  |
| `Assistant_Phone` | Assistant Phone Number |  |
| `Work_Phone` | Business Phone Number |  |
| `Work_Phone2` | Business 2 Phone Number |  |
| `Work_Fax` | Business Fax Number |  |
| `Callback` | Callback Phone Number |  |
| `Car_Phone` | Car Phone Number |  |
| `Company_Phone` | Company Phone Number |  |
| `Home_Phone` | Home Phone Number |  |
| `Home_Phone2` | Home 2 Phone Number |  |
| `Home_Fax` | Home Fax Number |  |
| `ISDN` | ISDN Number |  |
| `Cellular_Phone` | Mobile Phone Number |  |
| `Other_Phone` | Other Phone Number |  |
| `Other_Fax` | Other Fax Number |  |
| `Pager` | Pager Number |  |
| `Primary_Phone` | Primary Phone Number |  |
| `Radio_Phone` | Radio Phone Number |  |
| `Telex` | Telex Number |  |
| `TTY_TDD_Phone` | TTY/TDD Phone Number |  |
| `Addr1` | Default Address Line 1 | Virtual field linking to the currently specified default address. |
| `Addr2` | Default Address Line 2 | Same as Addr1 |
| `Addr3` | Default Address Line 3 | Same as Addr1 |
| `City` | Default City | Same as Addr1 |
| `State` | Default State | Same as Addr1 |
| `Zip` | Default Zip | Same as Addr1 |
| `Country` | Default Country | Same as Addr1 |
| `Phone1` | Phone Number Displayed (top-left) | Virtual field linking to the phone record currently displayed. |
| `Phone2` | Phone Number Displayed (bottom-left) | Same as Phone 1 |
| `Phone3` | Phone Number Displayed (top-right) | Same as Phone 1 |
| `Phone4` | Phone Number Displayed (bottom-right) | Same as Phone 1 |
| `PM_Integration` | * |  |
| `IsClientSw` | * |  |
| `IsVendorSw` | * |  |
| `IsPayeeSw` | * |  |
| `IsUserSw` | * |  |
| `Portal_LinkSentDate` | Client Portal Link Sent Date |  |
| `Portal_LinkSentTime` | Client Portal Link Sent Time |  |
| `Portal_Status` | Client Portal Status |  |
| `Email_Address1` | Email 1 Address |  |
| `Email_Address2` | Email 2 Address |  |
| `Email_Address3` | Email 3 Address |  |
| `Web_Page` | Web Page URL |  |
| `Comments` | Comments |  |
| `LP_CONTACT_ID` | LawPay Contact ID |  |
| `Portal_Email` | Client Portal Email |  |
| `Portal_Password` | * |  |
| `Portal_LinkGuid` | * |  |
| `TwoFASecret` | * |  |

### Tabs3 Billing: `COST`

- Title: Cost File (COST)
- Field count: 30
- Source: `tbmain/odbc_file_list___cost_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `REF_NUM` | Ref # |  |
| `DATE` | Date of Transaction |  |
| `TCODE` | Tcode | 1-999 |
| `TCODE_TYPE` | Tcode Type | 0-9 |
| `AMOUNT` | Amount |  |
| `HOLD` | Status | H=Hold P=Print U=Ready to be Updated S=Save s=Final billed save |
| `SOURCE` | Source of Transaction | B=Billing b=Split Billing R=Remote D=Data Capture Device P or C=PracticeMaster or Tabs3 Connect A=Accounts Payable |
| `USER_ID` | User ID |  |
| `CREATE_DATE` | Date Entered |  |
| `CREATE_TIME` | Time transaction was created |  |
| `TIMEKEEPER` | Timekeeper Number |  |
| `BILL_CODE` | Bill Code | 0-4 |
| `RATE_CODE` | Rate Code | 0-9 |
| `SALES_TAX` | Sales Tax Code | 0-9 |
| `PHASE_TASK` | Phase/Task Code for Task Code Billing | AA001 through ZZ999 |
| `ACTIVITY` | Activity or Expense Code | A001-A999, E001-E999, X001-X999 |
| `EXPENSE_ADVANCE` | Expense/Advance Indicator | E=Expense A=Advance |
| `ORIGINAL_AMT` | Amount field stored to calculate write-up or write-down figures |  |
| `COST_TYPE` | Cost Type | 0-9 for cost |
| `UNITS` | Units |  |
| `RATE` | Rate |  |
| `MIRROR_SEQ_NO` | * |  |
| `QBEditSeq` | * |  |
| `QBTxnID` | * |  |
| `QBVendor` | QuickBooks Vendor |  |
| `QBAcct` | QuickBooks Debit Account |  |
| `ORIG_TRANS_SEQ_NO` | * |  |
| `DESCRIPTION` | Description | maximum 5000 characters |

### Tabs3 Billing: `COSTRATE`

- Title: Cost Rate File (COSTRATE)
- Field count: 11
- Source: `tbmain/odbc_file_list___cost_rate_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `SHARED_SWITCH` | Shared Switch | 0=unchecked, 1=checked |
| `SHARED_CLIENT_ID` | Shared Client ID |  |
| `FIRST_RECORD` | First Switch | 0=false, 1=true |
| `TCODE` | Transaction Code |  |
| `ACTIVITY` | Activity Code |  |
| `RATE` | Rate |  |
| `NEW_RATE` | New Rate |  |
| `COST_DATE` | Effective Date of New Rate |  |
| `BILLABLE` | Billable |  |

### Tabs3 Billing: `EMPLOYEE`

- Title: Employee File (EMPLOYEE)
- Field count: 32
- Source: `tbmain/odbc_file_list___employee_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `EMPL` | Timekeeper Number |  |
| `USER` | User |  |
| `NAME` | Timekeeper Name | Virtual field from linked Contact |
| `INITIALS` | Initials | Virtual field from linked Contact |
| `Inactive` | * |  |
| `RATE1` | Hourly Rate 1 |  |
| `RATE2` | Hourly Rate 2 |  |
| `RATE3` | Hourly Rate 3 |  |
| `RATE4` | Hourly Rate 4 |  |
| `RATE5` | Hourly Rate 5 |  |
| `RATE6` | Hourly Rate 6 |  |
| `FEE_DATE` | Effective Date of New Rates |  |
| `NEW_RATE1` | New Rate 1 |  |
| `NEW_RATE2` | New Rate 2 |  |
| `NEW_RATE3` | New Rate 3 |  |
| `NEW_RATE4` | New Rate 4 |  |
| `NEW_RATE5` | New Rate 5 |  |
| `NEW_RATE6` | New Rate 6 |  |
| `LEVEL` | Timekeeper Level | 1-9 |
| `MONTHLY_COST1` | Overhead Month 1 |  |
| `MONTHLY_COST2` | Overhead Month 2 |  |
| `MONTHLY_COST3` | Overhead Month 3 |  |
| `MONTHLY_COST4` | Overhead Month 4 |  |
| `MONTHLY_COST5` | Overhead Month 5 |  |
| `MONTHLY_COST6` | Overhead Month 6 |  |
| `MONTHLY_COST7` | Overhead Month 7 |  |
| `MONTHLY_COST8` | Overhead Month 8 |  |
| `MONTHLY_COST9` | Overhead Month 9 |  |
| `MONTHLY_COST10` | Overhead Month 10 |  |
| `MONTHLY_COST11` | Overhead Month 11 |  |
| `MONTHLY_COST12` | Overhead Month 12 |  |

### Tabs3 Billing: `FEE`

- Title: Fee File (FEE)
- Field count: 28
- Source: `tbmain/odbc_file_list___fee_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `REF_NUM` | Ref # |  |
| `DATE` | Date of Transaction |  |
| `TCODE` | Tcode | 1-999 |
| `TCODE_TYPE` | Tcode Type | 0-9 |
| `AMOUNT` | Amount |  |
| `HOLD` | Status | H=Hold P=Print U=Ready to be Updated S=Save s=Final billed save |
| `SOURCE` | Source of Transaction | B=Billing b=Split Billing R=Remote D=Data Capture Device P or C=PracticeMaster or Tabs3 Connect A=Accounts Payable |
| `USER_ID` | User ID |  |
| `CREATE_DATE` | Date Entered |  |
| `CREATE_TIME` | Time transaction was created |  |
| `TIMEKEEPER` | Timekeeper Number |  |
| `BILL_CODE` | Bill Code | 0-4 |
| `RATE_CODE` | Rate Code | 0-9 |
| `SALES_TAX` | Sales Tax Code | 0-9 |
| `PHASE_TASK` | Phase/Task Code for Task Code Billing | AA001 through ZZ999 |
| `ACTIVITY` | Activity or Expense Code | A001-A999, E001-E999, X001-X999 |
| `RATE` | Rate |  |
| `HOURS` | Hours to Bill |  |
| `CATEGORY` | Category | 0-999 |
| `WORKED_HOURS` | Worked Hours |  |
| `MIRROR_SEQ_NO` | * |  |
| `QBEditSeq` | * |  |
| `QBTxnID` | * |  |
| `ORIG_TRANS_SEQ_NO` | * |  |
| `LOCATION` | Geolocation |  |
| `DESCRIPTION` | Description | maximum 5000 characters |

### Tabs3 Billing: `FUND`

- Title: Client Funds File (FUND)
- Field count: 18
- Source: `tbmain/odbc_file_list___client_funds_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `REFERENCE_NUMBER` | Ref # |  |
| `RECORD_TYPE` | Record Type | D=Deposit W=Withdrawal P=Payment to Firm |
| `FUND_APPLICATION` | Fund Application | F=Apply to Fees C=Costs E=Expenses A=Advances L=All |
| `DATE` | Date |  |
| `TCODE` | Tcode | 0-999 |
| `AMOUNT` | Amount |  |
| `STATUS` | Status | H=Hold P=Print U=Ready to be Updated |
| `USER_ID` | User ID |  |
| `CREATE_DATE` | Date Entered |  |
| `CREATE_TIME` | Time transaction was created |  |
| `GL_REF` | Reference Field (defaults to Client ID) |  |
| `PYMT_SEQNO` | Sequence # of linked payment transaction |  |
| `LP_TRANSACTION_ID` | LawPay Transaction ID |  |
| `DESCRIPTION` | Description | maximum 5000 characters |
| `CC_TRANS_ID` | PayFuse Transaction ID |  |
| `PP_PAYMENT_ID` | LexCharge Payment ID |  |

### Tabs3 Billing: `LEDGER`

- Title: Client Ledger File (LEDGER)
- Field count: 65
- Source: `tbmain/odbc_file_list___client_ledger_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `REFERENCE_NUMBER` | Reference Number |  |
| `RECORD_TYPE` | Record Type | S=Statement P=Payment W=Write Off |
| `DATE` | Date |  |
| `STATEMENT_NUMBER` | Statement Number |  |
| `STATEMENT_ID` | Sequence Number of statement record is included on |  |
| `APPLYTO_STMT_ID` | Sequence Number of statement record is applied to |  |
| `STATEMENT_TYPE` | * |  |
| `PAID_SW` |  |  |
| `FEE_AMOUNT_DUE` | Fees Billed (statement and write off records) | Amount Left to Apply (payment record) |
| `FEE_BALANCE` | Fees Due |  |
| `NONBILL_FEES` | Non-billable Fees |  |
| `FEE_WRITEUP` | Fee Write-Up Amount |  |
| `FEE_WRITEDOWN` | Fee Write-Down Amount |  |
| `FEE_TAX_AMOUNT_DUE` | Fee Tax Billed |  |
| `FEE_TAX_BALANCE` | Fee Tax Due |  |
| `FEE_COURTESY_DISCNT` | Fee Courtesy Discount |  |
| `BILLABLE_HOURS` | Billable Hours |  |
| `NONBILLABLE_HOURS` | Non-billable Hours |  |
| `WORKED_BILL_HOURS` | Worked Billable Hours |  |
| `WORKED_NONBILL_HOURS` | Worked Non-Billable Hours |  |
| `FEE_WRITEUP_HOURS` | Fee Write-Up Hours |  |
| `FEE_WRITEDOWN_HOURS` | Fee Write-Down Hours |  |
| `FEE_WUP_HRS_VALUE` | Fee Write-Up Hours Value |  |
| `FEE_WDN_HRS_VALUE` | Fee Write-Down Hours Value |  |
| `EXPENSE_AMOUNT_DUE` | Expenses Billed |  |
| `EXPENSE_BALANCE` | Expenses Due |  |
| `NONBILL_EXPENSES` | Non-billable Expenses |  |
| `EXPENSE_WRITEUP` | Expense Write-Up |  |
| `EXPENSE_WRITEDOWN` | Expense Write-Down |  |
| `EXP_TAX_AMOUNT_DUE` | Expense Tax Billed |  |
| `EXP_TAX_BALANCE` | Expense Tax Due |  |
| `ADVANCE_AMOUNT_DUE` | Advances Billed |  |
| `ADVANCE_BALANCE` | Advances due |  |
| `NONBILL_ADVANCES` | Non-billable Advances |  |
| `ADVANCE_WRITEUP` | Advance Write-Up |  |
| `ADVANCE_WRITEDOWN` | Advance Write-Down |  |
| `ADV_TAX_AMOUNT_DUE` | Advance Tax Billed |  |
| `ADV_TAX_BALANCE` | Advance Tax Due |  |
| `FINCHG_AMOUNT_DUE` | Finance Charge Billed |  |
| `FINCHG_BALANCE` | Finance Charge Due |  |
| `TOTAL_BALANCE` | Balance Due |  |
| `PAYMENT_TYPE` | Payment Type | R=Regular F=Fee E=Expense A=Advance |
| `PAYMENT_AMOUNT` | Payment Amount |  |
| `PAYMENT_LEFT` | Payment Amount Unapplied |  |
| `REFUND_AMOUNT` | Refund Amount |  |
| `UNAPPLIED_REFUND_AMT` | Refund Amount Unapplied |  |
| `PYMT_LINK` | Sequence Number of Payment Ledger record in Work-in-Process |  |
| `FUND_SW` | * |  |
| `FEE_AMOUNT` | Amount of Fees |  |
| `EXPENSE_AMOUNT` | Expenses |  |
| `ADVANCE_AMOUNT` | Advances |  |
| `FEE_TAX_AMOUNT` | Fee Tax Amount |  |
| `EXP_TAX_AMOUNT` | Expense Tax Amount |  |
| `ADV_TAX_AMOUNT` | Advance Tax Amount |  |
| `FINCHG_AMOUNT` | Finance Charge Amount |  |
| `TOTAL_AMOUNT` | Amount Billed |  |
| `APPLYTO_TKPR_NUM` | Timekeeper Number ledger record applies to |  |
| `APPLYTO_STMT_NUM` | Statement Number ledger record applies to |  |
| `REVERSED_SW` | Reversed Payment Switch | 0=unchecked, 1 = checked |
| `FEE_DESC_LINK` | * |  |
| `PREVIOUS_BALANCE` | Previous Balance |  |
| `UPDATE_MONTH` | Reporting Month Statement Updated In | 1-12 |
| `UPDATE_YEAR` | Reporting Year Statement Updated In | 00-99 ( 24 = 2024 , 97 = 1997, etc.) |

### Tabs3 Billing: `PAYMENT`

- Title: Payment File (PAYMENT)
- Field count: 25
- Source: `tbmain/odbc_file_list___payment_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `REF_NUM` | Ref # |  |
| `DATE` | Date of Transaction |  |
| `TCODE` | Tcode | 1-999 |
| `TCODE_TYPE` | Tcode Type | 0-9 |
| `AMOUNT` | Amount |  |
| `HOLD` | Status | H=Hold P=Print U=Ready to be Updated |
| `SOURCE` | Source of Transaction | B=Billing R=Remote D=Data Capture Device A=Accounts Payable T=Trust Accounting |
| `USER_ID` | User ID |  |
| `CREATE_DATE` | Date Entered |  |
| `CREATE_TIME` | Time transaction was created |  |
| `EXPENSE_ADVANCE` | Expense/Advance Indicator (if TCODE_TYPE = 2) | E=Expense A=Advance |
| `APPLYTO_STMT_NUM` | Statement Number for Payment Application |  |
| `APPLYTO_STMT_ID` | Sequence Number of Statement Number for Payment Application |  |
| `PAYMENT_LEFT` | Payment Amount Unapplied |  |
| `GL_CHECK_NUM` | Check Number |  |
| `GL_REF` | GL Reference | Defaults to Client ID |
| `GL_RECEIPT_TYPE` | Receipt Type | 0=Cash, 1=Check, 2=Credit Card, 3=Other Type, 4=Client Funds, 5=EFT |
| `TR_SEQNO` | Sequence # of transaction in Trust if payment originated in Trust |  |
| `CF_SEQNO` | Sequence # of linked client funds transaction |  |
| `LP_TRANSACTION_ID` | LawPay Transaction ID |  |
| `DESCRIPTION` | Description | maximum 5000 characters |
| `CC_TRANS_ID` | PayFuse Transaction ID |  |
| `PP_PAYMENT_ID` | LexCharge Payment ID |  |

### Trust Accounting: `CLIENT`

- Title: Client File (CLIENT)
- Field count: 135
- Source: `trmain/odbc_file_list___client_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `CLIENT_ID` | Client ID |  |
| `NAME` | Client Name |  |
| `CONTACT` | Contact Name |  |
| `ALPHA_SEARCH` | Name Search |  |
| `Client_Full_Name` | * |  |
| `Contact_Full_Name` | * |  |
| `Addr_No` | Address Selector | 1=Business 2=Home 3=Other |
| `Email_Addr_No` | Email Address Selector | 1-3 |
| `ADDR1` | * |  |
| `ADDR2` | * |  |
| `ADDR3` | * |  |
| `CITY` | * |  |
| `STATE` | * |  |
| `ZIP` | * |  |
| `COUNTRY` | * |  |
| `Phone1_Src` | Phone Number Selector (top-left) | Assistant_Phone Callback Car_Phone Cellular_Phone Company_Phone Home_Fax Home_Phone Home_Phone2 ISDN Other_Fax Other_Phone Pager Primary_Phone Radio_Phone Telex TTY_TDD_Phone Work_Fax Work_Phone Work_Phone2 |
| `Phone2_Src` | Phone Number Selector (top-right) | Same as PHONE1_SRC |
| `Phone3_Src` | Phone Number Selector (bottom-left) | Same as PHONE1_SRC |
| `Phome4_Src` | Phone Number Selector (bottom-right) | Same as PHONE1_SRC |
| `Phone1` | * |  |
| `Phone2` | * |  |
| `Phone3` | * |  |
| `Phone4` | * |  |
| `Phone` | * |  |
| `Fax_Phone` | * |  |
| `Home_Phone` | * |  |
| `Cellular_Phone` | * |  |
| `Alt_Addr1` | Address Line 1 | Secure/Matter Mode only |
| `Alt_Addr2` | Address Line 2 | Secure/Matter Mode only |
| `Alt_Addr3` | Address Line 3 | Secure/Matter Mode only |
| `Alt_City` | City | Secure/Matter Mode only |
| `Alt_State` | State | Secure/Matter Mode only |
| `Alt_Zip` | Zip | Secure/Matter Mode only |
| `Alt_Country` | Country | Secure/Matter Mode only |
| `Alt_Work_Phone` | Business Phone Number | Secure/Matter Mode only |
| `Alt_Work_Fax` | Fax Phone Number | Secure/Matter Mode only |
| `Alt_Home_Phone` | Home Phone Number | Secure/Matter Mode only |
| `Alt_Cellular_Phone` | Cellular Phone Number | Secure/Matter Mode only |
| `DESC` | Work Description |  |
| `MISC_1` | Miscellaneous Line 1 |  |
| `MISC_2` | Miscellaneous Line 2 |  |
| `MISC_3` | Miscellaneous Line 3 |  |
| `DATE_OPEN` | Open Date |  |
| `CLOSE_DATE` | Close Date |  |
| `BILLING_RATE_CODE` | Billing Rate Code |  |
| `HOURLY_RATE` | Hourly Rate |  |
| `PRIM_TKPR` | Primary Timekeeper # |  |
| `SEC_TKPR` | Secondary Timekeeper # |  |
| `ORIG_TKPR` | Originating Timekeeper # |  |
| `CATEGORY` | Category | 0-999 |
| `LEVEL_TYPE_CODE` | Timekeeper Level Type | R=Rate C=Code N=None |
| `LEVEL_HOURLY_RATE1` | Level Hourly Rate 1 (when LEVEL_TYPE_CODE = R) |  |
| `LEVEL_HOURLY_RATE2` | Level Hourly Rate 2 |  |
| `LEVEL_HOURLY_RATE3` | Level Hourly Rate 3 |  |
| `LEVEL_HOURLY_RATE4` | Level Hourly Rate 4 |  |
| `LEVEL_HOURLY_RATE5` | Level Hourly Rate 5 |  |
| `LEVEL_HOURLY_RATE6` | Level Hourly Rate 6 |  |
| `LEVEL_HOURLY_RATE7` | Level Hourly Rate 7 |  |
| `LEVEL_HOURLY_RATE8` | Level Hourly Rate 8 |  |
| `LEVEL_HOURLY_RATE9` | Level Hourly Rate 9 |  |
| `LEVEL_CODE1` | Level Code 1 (when LEVEL_TYPE_CODE = C) | 0-9 |
| `LEVEL_CODE2` | Level Code 2 | 0-9 |
| `LEVEL_CODE3` | Level Code 3 | 0-9 |
| `LEVEL_CODE4` | Level Code 4 | 0-9 |
| `LEVEL_CODE5` | Level Code 5 | 0-9 |
| `LEVEL_CODE6` | Level Code 6 | 0-9 |
| `LEVEL_CODE7` | Level Code 7 | 0-9 |
| `LEVEL_CODE8` | Level Code 8 | 0-9 |
| `LEVEL_CODE9` | Level Code 9 | 0-9 |
| `LOCATION` | Location |  |
| `BUDGET_HOURS` | Budget Hours |  |
| `BUDGET_AMOUNT` | Budget Amount |  |
| `DISCOUNT_TYPE` | Discount Type | P=Percentage A=Amount |
| `DISCOUNT_AMOUNT` | Discount Amount |  |
| `DISCOUNT_PERCENT` | Percentage Amount |  |
| `RESET_COURTESY_DISC` | Reset Courtesy Discount Switch | 0=unchecked, 1=checked |
| `TASK_BASED_BILLING` | Task Based Billing Switch | 0=unchecked, 1=checked |
| `INACTIVE` | Inactive Switch | 0=unchecked, 1=checked |
| `BILL_ON_DEMAND` | Bill On Demand Switch | 0=unchecked, 1=checked |
| `RELEASE_TO_BILL` | Release To Bill Switch | 0=unchecked, 1=checked |
| `PROGRESS_BILLING` | Progress Billing Switch | 0=unchecked, 1=checked |
| `BILLING_FREQUENCY` | Billing Frequency |  |
| `NONBILLABLE` | Non-Billable Switch | 0=unchecked, 1=checked |
| `BILL_TO_CODE` | Bill To Code | Y=Yes N=No D=Duplicate |
| `FINANCE_CHARGE` | Finance Charge Switch | 0=unchecked, 1=checked |
| `FINANCE_CHARGE_DAYS` | Finance Charge Days | 0-999 |
| `FINANCE_CHARGE_RATE` | Finance Charge Rate Code | 1-5 |
| `FEE_SALES_TAX_CODE` | Fee Sales Tax Code | 0-9 |
| `EXP_SALES_TAX_CODE` | Expense Sales Tax Code | 0-9 |
| `ADV_SALES_TAX_CODE` | Advance Sales Tax Code | 0-9 |
| `PYMT_ALLOC_METHOD` | Method to Apply Payments | 1-5 |
| `FINCHG_ALLOC_METHOD` | Apply Payments to Finance Charge | F,L |
| `RA_BY_INVOICE` | Receipt Allocation by Invoice Switch | 0=unchecked, 1=checked |
| `COVER_STATMENT` | Cover Statement Option | D=Detail Cover Statement S=Summary Cover Statement N=No Cover Statement I=Individual Detail Cover Statement J=Individual Summary Cover Statement |
| `COMBINE_MATTERS` | Combine Files | 0=unchecked, 1=checked |
| `TRUST_INTEGRATION` | Trust Integration | D=Detail S=Summary N=None |
| `DRAFT_STMT_CODES` | Draft Statement Template |  |
| `FINAL_STMT_CODES` | Final Statement Template |  |
| `PASSWORD_PROTECT_PDF` | Password Protect PDF Statements Switch | 0=unchecked, 1=checked |
| `PDF_PASSWORD` | Password |  |
| `FEE_REFERENCE_NUM` | * |  |
| `COST_REFERENCE_NUM` | * |  |
| `PYMT_REFERENCE_NUM` | * |  |
| `LEDG_REFERENCE_NUM` | * |  |
| `FUND_REFERENCE_NUM` | * |  |
| `STATEMENT_NUM` | Last Statement Number |  |
| `FUND_BALANCE` | Client Fund Balance |  |
| `REPLENISH_BELOW` | Replenish Below Amount |  |
| `REPLENISH_TO` | Replenish To Amount |  |
| `FUND_APPLICATION` | Fund Application for Client Funds | M=Manual F=Automatic Fee Pymt E=Automatic Expense Pymt A=Automatic Advance Pymt L=Automatic All |
| `ONE_TIME_FUND_BILL` | One Time Retainer Switch | 0=unchecked, 1=checked |
| `FUND_STMT_FORMAT` | Client Funds Statement Format | D=Detail S=Summary N=None |
| `SECURE_CLIENT` | Secure Client Switch | 0=unchecked, 1=checked |
| `MAIN_COVER_CLIENT` | Main Cover Statement Client Switch | 0=unchecked, 1=checked |
| `FEE_THRESHOLD` | Fee Threshold Amount |  |
| `EXP_THRESHOLD` | Expense Threshold Amount |  |
| `ADV_THRESHOLD` | Advance Threshold Amount |  |
| `THRESHOLD_BILLING` | Threshold Billing Items | I=Individual A=All T=Total |
| `TOTAL_THRESHOLD` | Total Threshold Amount |  |
| `RESET_BEG_NOTES` | Reset Beginning Statement Notes Switch | 0=unchecked, 1=checked |
| `RESET_END_NOTES` | Reset Ending Statement Notes Switch | 0=unchecked, 1=checked |
| `BUDGET_WARNING` | Budget Warning Switch | 0=unchecked, 1=checked |
| `QBEditSeq` | * |  |
| `QBListID` | * |  |
| `QB_INTEGRATION` | QuickBooks Integration Switch | 0=unchecked, 1=checked |
| `PM_INTEGRATION` | * |  |
| `SPLIT_FEE_CALC_AMT` | * |  |
| `SPLIT_FEE_CREDITS` | * |  |
| `SPLIT_FEE_ZERO_AMTS` | * |  |
| `SPLIT_COST_CALC_AMT` | * |  |
| `SPLIT_COST_CREDITS` | * |  |
| `SPLIT_COST_ZERO_AMTS` | * |  |
| `ALT_EMAIL_ADDRESS` | Email Address | Secure/Matter Mode only |
| `ALT_WEB_PAGE` | Web Page | Secure/Matter Mode only |

### Trust Accounting: `CONTACT`

- Title: Contact File (CONTACT)
- Field count: 79
- Source: `trmain/odbc_file_list___contact_file.htm`

| Field | Description | Notes |
|-|-|-|
| `_SEQUENCE_NO` | Internal Sequence Number |  |
| `RP_Key` | Contact Key |  |
| `Name` | Full Name |  |
| `Alt_Name` | * |  |
| `Calc_Name_Sw` | Update Full Name based on First, Middle, and Last Name fields | 0=Cleared 1=Selected |
| `First_Name` | First Name |  |
| `Middle_Name` | Middle Name |  |
| `Last_Name` | Last Name |  |
| `Initials` | Initials |  |
| `Organization` | Organization Name |  |
| `Org_Sw` | Organization Switch | 0=Individual 1=Organization |
| `Inactive` | Inactive | 0=Active 1=Inactive |
| `Addr_No` | Default Address Selector | 1=Home 2=Business 3=Other |
| `Phone1_Src` | Phone Number Selector (top-left) | Assistant_Phone Callback Car_Phone Cellular_Phone Company_Phone Home_Fax Home_Phone Home_Phone2 ISDN Other_Fax Other_Phone Pager Primary_Phone Radio_Phone Telex TTY_TDD_Phone Work_Fax Work_Phone Work_Phone2 |
| `Phone2_Src` | Phone Number Selector (bottom-left) | Same types as PHONE1_SRC |
| `Phone3_Src` | Phone Number Selector (top-right) | Same types as PHONE1_SRC |
| `Phone4_Src` | Phone Number Selector (bottom-right) | Same types as PHONE1_SRC |
| `Addr1_Line1` | Business Address Line 1 |  |
| `Addr1_Line2` | Business Address Line 2 |  |
| `Addr1_Line3` | Business Address Line 3 |  |
| `Addr1_City` | Business Address City |  |
| `Addr1_State` | Business Address State |  |
| `Addr1_Zip` | Business Address Zip |  |
| `Addr1_Country` | Business Address Country |  |
| `Addr2_Line1` | Home Address Line 1 |  |
| `Addr2_Line2` | Home Address Line 2 |  |
| `Addr2_Line3` | Home Address Line 3 |  |
| `Addr2_City` | Home Address City |  |
| `Addr2_State` | Home Address State |  |
| `Addr2_Zip` | Home Address Zip |  |
| `Addr2_Country` | Home Address Country |  |
| `Addr3_Line1` | Other Address Line 1 |  |
| `Addr3_Line2` | Other Address Line 2 |  |
| `Addr3_Line3` | Other Address Line 3 |  |
| `Addr3_City` | Other Address City |  |
| `Addr3_State` | Other Address State |  |
| `Addr3_Zip` | Other Address Zip |  |
| `Addr3_Country` | Other Address Country |  |
| `Assistant_Phone` | Assistant Phone Number |  |
| `Work_Phone` | Business Phone Number |  |
| `Work_Phone2` | Business 2 Phone Number |  |
| `Work_Fax` | Business Fax Number |  |
| `Callback` | Callback Phone Number |  |
| `Car_Phone` | Car Phone Number |  |
| `Company_Phone` | Company Phone Number |  |
| `Home_Phone` | Home Phone Number |  |
| `Home_Phone2` | Home 2 Phone Number |  |
| `Home_Fax` | Home Fax Number |  |
| `ISDN` | ISDN Number |  |
| `Cellular_Phone` | Mobile Phone Number |  |
| `Other_Phone` | Other Phone Number |  |
| `Other_Fax` | Other Fax Number |  |
| `Pager` | Pager Number |  |
| `Primary_Phone` | Primary Phone Number |  |
| `Radio_Phone` | Radio Phone Number |  |
| `Telex` | Telex Number |  |
| `TTY_TDD_Phone` | TTY/TDD Phone Number |  |
| `Addr1` | Default Address Line 1 | Virtual field linking to the currently specified default address. |
| `Addr2` | Default Address Line 2 | Same as Addr1 |
| `Addr3` | Default Address Line 3 | Same as Addr1 |
| `City` | Default City | Same as Addr1 |
| `State` | Default State | Same as Addr1 |
| `Zip` | Default Zip | Same as Addr1 |
| `Country` | Default Country | Same as Addr1 |
| `Phone1` | Default Phone 1 | Same as Addr1 |
| `Phone2` | Default Phone 2 | Same as Addr1 |
| `Phone3` | Default Phone 3 | Same as Addr1 |
| `Phone4` | Default Phone 4 | Same as Addr1 |
| `PM_Integration` | * |  |
| `IsClientSw` | * |  |
| `IsVendorSw` | * |  |
| `IsPayeeSw` | * |  |
| `IsUserSw` | * |  |
| `Email_Address1` | Default Email Address 1 | Same as Addr1 |
| `Email_Address2` | Default Email Address 2 | Same as Addr1 |
| `Email_Address3` | Default Email Address 3 | Same as Addr1 |
| `Web_Page` | Default Web Page | Same as Addr1 |
| `Comments` |  |  |
| `LP_CONTACT_ID` | LawPay Contact ID |  |

