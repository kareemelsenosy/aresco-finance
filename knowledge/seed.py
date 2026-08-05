"""Starter reference pack for the audit assistant.

IMPORTANT — read before relying on this in an audit response.

These entries are a working starting point, not a certified copy of the
standards. Article numbers, rates and thresholds change (EAS was last
comprehensively reissued by Ministerial Decree 69/2019; tax rates change with
each finance law). The finance team should verify every entry against the
official text and extend this store with ARESCO's own accounting policy manual
before the responses are sent to an auditor.

The agent is constrained to cite only what is in this store, so an entry that
is missing produces an explicit "reference not held" flag rather than an
invented citation. That is the intended behaviour — fix it by adding the
reference, never by loosening the constraint.
"""

SEED_DOCS = [
    # --- Egyptian Accounting Standards ---------------------------------
    {
        "framework": "EAS",
        "ref": "EAS 1",
        "title": "Presentation of Financial Statements",
        "tags": "presentation,disclosure,going concern,comparatives",
        "body": (
            "Prescribes the basis for presentation of general purpose financial "
            "statements: a complete set comprises a statement of financial position, "
            "statement of profit or loss and other comprehensive income, statement of "
            "changes in equity, statement of cash flows, and notes. Requires fair "
            "presentation, going concern assessment, accrual basis, materiality and "
            "aggregation, offsetting restrictions, and comparative information. "
            "Broadly converged with IAS 1."
        ),
    },
    {
        "framework": "EAS",
        "ref": "EAS 2",
        "title": "Inventories",
        "tags": "inventory,WIP,net realisable value,cost formula",
        "body": (
            "Inventories are measured at the lower of cost and net realisable value. "
            "Cost comprises purchase cost, conversion costs and other costs to bring "
            "inventories to their present location and condition. FIFO or weighted "
            "average cost formulas; LIFO is not permitted. Write-downs to NRV are "
            "recognised as an expense in the period; reversals are recognised as a "
            "reduction of the inventory expense. Relevant to steel stock and "
            "fabrication work in progress."
        ),
    },
    {
        "framework": "EAS",
        "ref": "EAS 10",
        "title": "Property, Plant and Equipment",
        "tags": "PPE,fixed assets,depreciation,impairment,revaluation",
        "body": (
            "Recognition at cost; subsequent measurement under the cost model or the "
            "revaluation model. Depreciation is allocated systematically over the "
            "useful life, with the residual value, useful life and depreciation "
            "method reviewed at least at each financial year end. Derecognition on "
            "disposal or when no future economic benefits are expected."
        ),
    },
    {
        "framework": "EAS",
        "ref": "EAS 13",
        "title": "The Effects of Changes in Foreign Exchange Rates",
        "tags": "FX,foreign currency,translation,monetary items",
        "body": (
            "Foreign currency transactions are recorded at the spot rate on the "
            "transaction date. At each reporting date monetary items are retranslated "
            "at the closing rate, non-monetary items at historical cost remain at the "
            "transaction-date rate, and non-monetary items at fair value use the rate "
            "at the fair value measurement date. Exchange differences on monetary "
            "items are recognised in profit or loss. Directly relevant to USD, EUR and "
            "AED bank balances and receivables."
        ),
    },
    {
        "framework": "EAS",
        "ref": "EAS 24",
        "title": "Income Taxes",
        "tags": "tax,deferred tax,temporary differences",
        "body": (
            "Current tax is measured at the amount expected to be paid using enacted "
            "or substantively enacted rates. Deferred tax is recognised on temporary "
            "differences between the carrying amount and the tax base of assets and "
            "liabilities, using the liability method. Deferred tax assets are "
            "recognised only to the extent that future taxable profit will be "
            "available. Deferred tax is not discounted."
        ),
    },
    {
        "framework": "EAS",
        "ref": "EAS 42",
        "title": "Consolidated Financial Statements",
        "tags": "consolidation,control,subsidiary,NCI",
        "body": (
            "A parent presents consolidated financial statements covering all entities "
            "it controls. Control exists where the investor has power over the "
            "investee, exposure to variable returns, and the ability to use its power "
            "to affect those returns. Uniform accounting policies and a common "
            "reporting date are required; intragroup balances, transactions, income "
            "and expenses are eliminated in full. Non-controlling interests are "
            "presented within equity, separately from parent shareholders' equity."
        ),
    },
    {
        "framework": "EAS",
        "ref": "EAS 47",
        "title": "Financial Instruments",
        "tags": "financial instruments,ECL,impairment,classification,receivables",
        "body": (
            "Classification of financial assets by business model and contractual cash "
            "flow characteristics (amortised cost, FVOCI, FVTPL). Introduces the "
            "expected credit loss impairment model in place of incurred loss. For "
            "trade receivables without a significant financing component the "
            "simplified approach applies: lifetime expected credit losses are "
            "recognised from initial recognition, commonly measured using a provision "
            "matrix. Broadly converged with IFRS 9."
        ),
    },
    {
        "framework": "EAS",
        "ref": "EAS 48",
        "title": "Revenue from Contracts with Customers",
        "tags": "revenue,POC,over time,performance obligation,contract asset",
        "body": (
            "Five-step model: identify the contract; identify performance obligations; "
            "determine the transaction price; allocate the price to the performance "
            "obligations; recognise revenue when (or as) each obligation is satisfied. "
            "Revenue is recognised over time where the customer simultaneously "
            "receives and consumes the benefits, the entity's performance creates or "
            "enhances an asset the customer controls, or the asset has no alternative "
            "use and the entity has an enforceable right to payment for performance "
            "completed to date. Fabrication and erection contracts typically meet the "
            "third criterion and are recognised over time using an input or output "
            "measure of progress. Contract assets, contract liabilities and retentions "
            "are presented separately from receivables. Broadly converged with IFRS 15."
        ),
    },
    {
        "framework": "EAS",
        "ref": "EAS 49",
        "title": "Leases",
        "tags": "leases,right of use,lease liability",
        "body": (
            "A lessee recognises a right-of-use asset and a lease liability for "
            "substantially all leases, with recognition exemptions for short-term "
            "leases and leases of low-value assets. The liability is measured at the "
            "present value of unpaid lease payments discounted at the rate implicit in "
            "the lease or, if not readily determinable, the incremental borrowing rate. "
            "Broadly converged with IFRS 16. Relevant to equipment, vehicle and "
            "building rentals."
        ),
    },

    # --- IFRS ------------------------------------------------------------
    {
        "framework": "IFRS",
        "ref": "IFRS 9.5.5.15",
        "title": "Simplified approach for trade receivables",
        "tags": "ECL,simplified approach,trade receivables,lifetime",
        "body": (
            "An entity shall always measure the loss allowance at an amount equal to "
            "lifetime expected credit losses for trade receivables and contract assets "
            "that do not contain a significant financing component. Where they do "
            "contain a significant financing component, the entity may elect as an "
            "accounting policy to apply the simplified approach, applied consistently."
        ),
    },
    {
        "framework": "IFRS",
        "ref": "IFRS 9.5.5.17",
        "title": "Measurement basis for expected credit losses",
        "tags": "ECL,measurement,forward looking,probability weighted",
        "body": (
            "Expected credit losses shall be measured in a way that reflects: (a) an "
            "unbiased and probability-weighted amount determined by evaluating a range "
            "of possible outcomes; (b) the time value of money; and (c) reasonable and "
            "supportable information that is available without undue cost or effort "
            "about past events, current conditions and forecasts of future economic "
            "conditions. Requirement (c) is the basis for a forward-looking overlay on "
            "historical loss rates."
        ),
    },
    {
        "framework": "IFRS",
        "ref": "IFRS 9.B5.5.35",
        "title": "Provision matrix",
        "tags": "ECL,provision matrix,aging,loss rate",
        "body": (
            "A provision matrix is an example of a practical expedient for measuring "
            "expected credit losses on trade receivables: fixed provision rates are "
            "applied depending on the number of days a receivable is past due. An "
            "entity would use its historical credit loss experience, adjusted for "
            "forward-looking estimates, and would group receivables where they share "
            "credit risk characteristics such as geography, product type, customer "
            "rating or collateral."
        ),
    },
    {
        "framework": "IFRS",
        "ref": "IFRS 7.35",
        "title": "Credit risk disclosures",
        "tags": "disclosure,credit risk,ECL,concentration",
        "body": (
            "Requires disclosure of information enabling users to understand the effect "
            "of credit risk on the amount, timing and uncertainty of future cash flows: "
            "the inputs, assumptions and techniques used to measure ECL; a "
            "reconciliation of the opening to closing loss allowance; gross carrying "
            "amounts by credit risk rating grade; and concentrations of credit risk."
        ),
    },
    {
        "framework": "IFRS",
        "ref": "IFRS 15.35",
        "title": "Revenue recognised over time",
        "tags": "revenue,over time,POC,progress",
        "body": (
            "An entity transfers control over time, and satisfies a performance "
            "obligation over time, if one of the following is met: (a) the customer "
            "simultaneously receives and consumes the benefits as the entity performs; "
            "(b) the entity's performance creates or enhances an asset the customer "
            "controls as it is created; (c) the entity's performance does not create an "
            "asset with an alternative use and the entity has an enforceable right to "
            "payment for performance completed to date."
        ),
    },
    {
        "framework": "IFRS",
        "ref": "IAS 1.25",
        "title": "Going concern",
        "tags": "going concern,liquidity,disclosure",
        "body": (
            "Management shall make an assessment of the entity's ability to continue as "
            "a going concern, taking into account all available information about the "
            "future covering at least, but not limited to, twelve months from the end "
            "of the reporting period. Where material uncertainties related to events or "
            "conditions cast significant doubt on that ability, those uncertainties "
            "shall be disclosed. Negative working capital, net liability positions and "
            "recurring liquidity shortfalls are indicators requiring this assessment."
        ),
    },
    {
        "framework": "IFRS",
        "ref": "IAS 24",
        "title": "Related Party Disclosures",
        "tags": "related party,disclosure,group,key management",
        "body": (
            "Requires disclosure of relationships between a parent and its "
            "subsidiaries irrespective of whether transactions have occurred, key "
            "management personnel compensation, and for each category of related party: "
            "the amount of transactions, outstanding balances including commitments, "
            "their terms and conditions, any guarantees, provisions for doubtful debts "
            "on those balances, and the expense recognised in respect of bad or "
            "doubtful debts due from related parties."
        ),
    },

    # --- Egyptian Companies Law 159 of 1981 -----------------------------
    {
        "framework": "LAW_159",
        "ref": "Law 159/1981 — statutory reserve",
        "title": "Statutory reserve appropriation",
        "tags": "reserve,appropriation,retained earnings,distribution",
        "body": (
            "Joint stock companies are required to transfer a percentage of annual net "
            "profit to a legal (statutory) reserve until that reserve reaches a "
            "prescribed proportion of issued capital, after which the appropriation may "
            "cease. VERIFY the current percentage and cap against the operative text "
            "and the company's articles of association before citing in a response — "
            "the commonly applied figures are 5% of annual profit up to 50% of issued "
            "capital, but the articles may set a higher requirement."
        ),
    },
    {
        "framework": "LAW_159",
        "ref": "Law 159/1981 — losses exceeding half of capital",
        "title": "Action required where accumulated losses erode capital",
        "tags": "capital,losses,going concern,shareholders,equity deficit",
        "body": (
            "Where accumulated losses reach a specified proportion of issued capital, "
            "the board is obliged to convene an extraordinary general assembly to "
            "consider whether the company should continue or be dissolved, and the "
            "resolution must be published. A negative shareholders' equity position "
            "makes this a live legal question and not only an accounting disclosure — "
            "the auditor will ask what action the board has taken. VERIFY the exact "
            "threshold and procedural requirements against the operative text."
        ),
    },
    {
        "framework": "LAW_159",
        "ref": "Law 159/1981 — auditor and financial statements",
        "title": "Appointment of auditor and approval of accounts",
        "tags": "auditor,general assembly,approval,filing",
        "body": (
            "The ordinary general assembly appoints the auditor and fixes the fee, "
            "receives the board report and the auditor's report, and approves the "
            "financial statements. Statutory deadlines apply to holding the assembly "
            "after the financial year end and to filing the approved accounts. VERIFY "
            "the current deadlines with the company's legal counsel."
        ),
    },
    {
        "framework": "LAW_159",
        "ref": "Law 159/1981 — related party transactions",
        "title": "Board interest and related party dealings",
        "tags": "related party,board,conflict of interest,approval",
        "body": (
            "Transactions in which a board member has a direct or indirect interest "
            "require disclosure to the board and, in defined cases, authorisation by "
            "the general assembly. Given the volume of related-party revenue and "
            "intercompany balances in the group, expect the auditor to test both the "
            "accounting disclosure under IAS 24 / EAS and the corporate authorisation "
            "trail under the companies law."
        ),
    },

    # --- Egyptian tax ----------------------------------------------------
    {
        "framework": "TAX",
        "ref": "Income Tax Law 91/2005 — corporate rate",
        "title": "Corporate income tax",
        "tags": "corporate tax,rate,taxable profit",
        "body": (
            "Corporate profits are taxed under Income Tax Law 91/2005 and its "
            "amendments. The standard corporate rate has been 22.5% of net taxable "
            "profit; special rates apply to oil and gas exploration and production and "
            "to the Suez Canal Authority and Central Bank. Taxable profit is accounting "
            "profit adjusted for non-deductible expenses, tax depreciation and exempt "
            "income. VERIFY the rate and the current adjustment rules for the year "
            "under audit — finance laws amend these frequently."
        ),
    },
    {
        "framework": "TAX",
        "ref": "VAT Law 67/2016",
        "title": "Value Added Tax",
        "tags": "VAT,indirect tax,input tax,schedule tax",
        "body": (
            "VAT applies to the supply of goods and services and to imports. The "
            "standard rate has been 14%, with a zero rate for exports and specified "
            "exemptions; certain goods and services are subject to schedule tax "
            "instead of or in addition to VAT. Input tax on inputs used in making "
            "taxable supplies is generally creditable, subject to documentation and "
            "timing rules. Monthly returns and payment are required. VERIFY rates and "
            "the treatment of construction and fabrication services for the year under "
            "audit."
        ),
    },
    {
        "framework": "TAX",
        "ref": "Withholding tax on local payments",
        "title": "Local withholding and remittance",
        "tags": "WHT,withholding,contractors,supplies,remittance",
        "body": (
            "Payments to local suppliers and contractors above a de minimis threshold "
            "are subject to withholding at rates that vary by the nature of the payment "
            "(supplies, contracting, services, commissions and brokerage). Amounts "
            "withheld must be remitted and reported to the Egyptian Tax Authority "
            "quarterly, and are credited against the supplier's income tax. Failure to "
            "withhold exposes the payer, not only the payee. VERIFY the current rates "
            "and threshold."
        ),
    },
    {
        "framework": "TAX",
        "ref": "Payroll tax and social insurance",
        "title": "Employment taxes",
        "tags": "payroll,salary tax,social insurance,accrual",
        "body": (
            "Salary tax is withheld monthly by the employer on a progressive scale and "
            "remitted to the Egyptian Tax Authority, with an annual reconciliation. "
            "Social insurance contributions are payable by both employer and employee "
            "on insured earnings within statutory floors and ceilings under the Social "
            "Insurance and Pensions Law. Unremitted balances and accrued but unpaid "
            "contributions are a standard audit focus and are typically disclosed "
            "within governmental dues."
        ),
    },
    {
        "framework": "TAX",
        "ref": "Tax evaders register",
        "title": "Published register of tax evaders",
        "tags": "tax evaders,counterparty screening,due diligence,input tax",
        "body": (
            "The Egyptian Tax Authority publishes lists of persons convicted of or "
            "formally charged with tax evasion. Transacting with a listed counterparty "
            "carries the risk that input tax credits and expense deductions claimed on "
            "those transactions are challenged, and raises a reputational and "
            "compliance question the auditor may probe. Screen customers and suppliers "
            "against the published list and retain evidence of the screening. Load the "
            "current list into the tax evader register in this application — it is not "
            "pre-populated, because the list changes and must come from the official "
            "source."
        ),
    },

    # --- ARESCO-specific context ----------------------------------------
    {
        "framework": "EAS",
        "ref": "ARESCO — revenue recognition policy",
        "title": "Company policy: fabrication and erection contracts",
        "tags": "policy,revenue,POC,ARESCO,retention",
        "body": (
            "PLACEHOLDER — replace with ARESCO's documented accounting policy. The "
            "audit assistant will cite whatever is recorded here as the company's "
            "stated policy, so an inaccurate entry produces an inaccurate response. "
            "Record at minimum: the measure of progress used for over-time contracts, "
            "the treatment of variation orders and claims, the point at which retention "
            "receivables are recognised and reclassified, and the treatment of advance "
            "payments received."
        ),
    },
]
