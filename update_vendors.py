"""Apply the vendor master to invoice_db.vendors. Generated from
"Vendor Master (1)-c0b20127.xlsx". Updates existing records only - never inserts.

    python update_vendors.py            # show what would change
    python update_vendors.py --apply    # actually change it
"""
import sys

from pymongo import MongoClient, UpdateOne

from config import settings

UPDATES = [
    {
        "vendor_id": "b877eb43-e75c-4ae8-ba4a-5cb295744931",
        "name": "AEM BASE PRIVATE LIMITED",
        "db_name": "AEM BASE PRIVATE LIMITED",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "Canara Bank",
                "ifsc": "CNRB0002769",
                "account_number": "2769201000469"
            }
        }
    },
    {
        "vendor_id": "b6412aec-f6c6-49ad-9691-bb94c7141c4e",
        "name": "Akbar Travels",
        "db_name": "Akbar Travels",
        "set": {
            "bank_details": {
                "bank": "BOB Bank OD A/C",
                "ifsc": "BARB0NUNGAM",
                "account_number": "08040400000427"
            }
        }
    },
    {
        "vendor_id": "5cc94159-9438-4c93-9328-56fd94ef8939",
        "name": "Aurora Social",
        "db_name": "Aurora Social",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.02,
            "bank_details": {
                "bank": "HDFC Bank",
                "ifsc": "HDFC0005675",
                "account_number": "50200095287279"
            }
        }
    },
    {
        "vendor_id": "476bb08d-306d-4bbd-9330-d3e42a6449f0",
        "name": "BTB Venture",
        "db_name": "BTB Venture",
        "set": {
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "AXIS Bank",
                "ifsc": "UTIB0000338",
                "account_number": "917020077987540"
            }
        }
    },
    {
        "vendor_id": "5529b102-9c93-40c0-afe0-6b19f164cf94",
        "name": "C R SANJAY and Co",
        "db_name": "C R Sanjay and Co",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "SBI Bank",
                "ifsc": "SBIN0003357",
                "account_number": "30181144725"
            }
        }
    },
    {
        "vendor_id": "366e54f4-f44a-468b-ae5f-d8d97b2b99a6",
        "name": "Cladus Consultech LLP",
        "db_name": "Cladus Consultech LLP",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "SBI Bank",
                "ifsc": "SBIN0064074",
                "account_number": "42487147708"
            }
        }
    },
    {
        "vendor_id": "d96a2d07-795d-4ca2-bfa4-1982568faddf",
        "name": "Cloudbash Technologies Pvt. Ltd.",
        "db_name": "INNOVATECTURE DESIGN SERVICES LLP",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "Kotak Bank",
                "ifsc": "KKBK0000431",
                "account_number": "8011457691"
            }
        }
    },
    {
        "vendor_id": "8944527e-9b96-4ac9-925b-35e7da443ecb",
        "name": "D Code Research",
        "db_name": "Dcode Research Services Pvt Ltd",
        "set": {
            "credit_days": 7,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "Kotak Bank",
                "ifsc": "KKBK0000433",
                "account_number": "5811159046"
            }
        }
    },
    {
        "vendor_id": "d8f2d0d8-1bdd-4e53-8617-6d6739f33205",
        "name": "Darts India Private Limited",
        "db_name": "Darts India Private Limited",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC Bank",
                "ifsc": "HDFC0000141",
                "account_number": "1412000004776"
            }
        }
    },
    {
        "vendor_id": "f5614eb5-6716-43a7-9d24-0accf6958e16",
        "name": "DataDriven Services",
        "db_name": "Data Driven Services",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "Kotak Bank",
                "ifsc": "KKBK0008494",
                "account_number": "8945142809"
            }
        }
    },
    {
        "vendor_id": "25d17a92-0505-4a99-9918-276fed882182",
        "name": "Deepak Joshi",
        "db_name": "Deepak Joshi",
        "set": {
            "credit_days": 7,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC",
                "ifsc": "HDFC0000693",
                "account_number": "50100449555146"
            }
        }
    },
    {
        "vendor_id": "0befb91f-4d1e-4403-93a1-e4535a40c499",
        "name": "Dhruv Services",
        "db_name": "Dhruv Services",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC Bank",
                "ifsc": "HDFC0000569",
                "account_number": "5692000003608"
            }
        }
    },
    {
        "vendor_id": "dd75e147-cbcd-4316-af5a-66491f268623",
        "name": "DigiArise",
        "db_name": "DigiArise",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "ICICI Bank",
                "ifsc": "ICIC0002448",
                "account_number": "244805000296"
            }
        }
    },
    {
        "vendor_id": "f72dfb27-b619-4caa-b92d-889720671c81",
        "name": "Divanshu Jagtani",
        "db_name": "Divanshu Jagtani",
        "set": {
            "credit_days": 7,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC BANK LTD.",
                "ifsc": "HDFC0001844",
                "account_number": "50100400000001"
            }
        }
    },
    {
        "vendor_id": "a2ac813f-2be7-4808-8832-7e8ea8408603",
        "name": "ElproDigital by FYE Digit Informatics LLP",
        "db_name": "FYE Digit Informatics LLP",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "ICICI Bank",
                "ifsc": "ICIC0003170",
                "account_number": "317005000000"
            }
        }
    },
    {
        "vendor_id": "a2ac813f-2be7-4808-8832-7e8ea8408603",
        "name": "FYE Digit Informatics LLP",
        "db_name": "FYE Digit Informatics LLP",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "ICICI Bank",
                "ifsc": "ICIC0003170",
                "account_number": "317005001361"
            }
        }
    },
    {
        "vendor_id": "1fa90b93-fd94-4b55-bf0f-05e7e43e31e8",
        "name": "Harish Ganapathi T K G",
        "db_name": "Harish Ganapathi T K G",
        "set": {
            "credit_days": 7,
            "bank_details": {
                "bank": "SBI Bank",
                "ifsc": "SBIN0007482",
                "account_number": "20195021948"
            }
        }
    },
    {
        "vendor_id": "5bfd6ed4-bc72-4598-8a9a-37041ae6fcf9",
        "name": "HEXANOVATE PRIVATE LIMITED",
        "db_name": "Hexanovate Private Limited",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.02,
            "bank_details": {
                "bank": "HDFC Bank",
                "ifsc": "HDFC0000039",
                "account_number": "50200083333201"
            }
        }
    },
    {
        "vendor_id": "d96a2d07-795d-4ca2-bfa4-1982568faddf",
        "name": "INNOVATECTURE DESIGN SERVICES LLP",
        "db_name": "INNOVATECTURE DESIGN SERVICES LLP",
        "set": {
            "credit_days": 1,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC Bank",
                "ifsc": "HDFC0001993",
                "account_number": "59294487210786"
            }
        }
    },
    {
        "vendor_id": "c51e5500-f294-4c92-9e0b-67b245e38b20",
        "name": "Inovatrik Technologies Private Limited",
        "db_name": "Inovatrik Technologies Private Limited",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC",
                "ifsc": "HDFC0001993",
                "account_number": "50200044310832"
            }
        }
    },
    {
        "vendor_id": "f3efac45-134a-455d-b388-4e2982ff5a70",
        "name": "Intime Solutions",
        "db_name": "Intime Solutions",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "BANK OF INDIA",
                "ifsc": "BKID0008438",
                "account_number": "843820110000546"
            }
        }
    },
    {
        "vendor_id": "e0ae5ccc-515f-4418-ad37-b51ef9d58f82",
        "name": "Leadle Consulting",
        "db_name": "Leadle Consulting",
        "set": {
            "credit_days": 7,
            "tds_rate": 0.02,
            "bank_details": {
                "bank": "HDFC",
                "ifsc": "HDFC0000444",
                "account_number": "50200017071374"
            }
        }
    },
    {
        "vendor_id": "8fc56baa-3771-4abb-a196-3fdaaf9ca455",
        "name": "LUBUS",
        "db_name": "LUBUS",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.02,
            "bank_details": {
                "bank": "Union bank of India",
                "ifsc": "UBIN0904554",
                "account_number": "637601010050124"
            }
        }
    },
    {
        "vendor_id": "73379a9c-55ac-4c8a-b793-1b75671a997f",
        "name": "Mediavak India Private Limited",
        "db_name": "Mediavak",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "ICICI Bank",
                "ifsc": "ICIC0000009",
                "account_number": "000905026841"
            }
        }
    },
    {
        "vendor_id": "2bd67e77-ea8f-494e-88b7-77f510af960e",
        "name": "Meeting Mind Infosystems",
        "db_name": "Meeting Minds infosystems",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "Karnataka Bank",
                "ifsc": "KARB0000107",
                "account_number": "1072000110077201"
            }
        }
    },
    {
        "vendor_id": "8845f3a8-35b9-4d17-b89a-5c0167eed428",
        "name": "MITA DIGAMBER MANDAWKER",
        "db_name": "MITA DIGAMBER MANDAWKER",
        "set": {
            "credit_days": 7,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC BANK",
                "ifsc": "HDFC0001120",
                "account_number": "50100237028403"
            }
        }
    },
    {
        "vendor_id": "bc699271-3447-422a-8f73-bb1ca1fb49f1",
        "name": "MOHAMMAD KAISER PERWEZ",
        "db_name": "MOHAMMAD KAISER PERWEZ",
        "set": {
            "credit_days": 7,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC",
                "ifsc": "HDFC0000243",
                "account_number": "50100259252446"
            }
        }
    },
    {
        "vendor_id": "be835d55-c67b-4fe1-84d1-d74d29ed7428",
        "name": "OnGraph Technologies Pvt.",
        "db_name": "OnGraph Technologies Pvt.",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.02,
            "bank_details": {
                "bank": "HDFC BANK",
                "ifsc": "HDFC0000088",
                "account_number": "00882320004452"
            }
        }
    },
    {
        "vendor_id": "c38631ca-6fe5-4e3f-9e63-4eb26c463acc",
        "name": "Priya Jain",
        "db_name": "Priya Jain",
        "set": {
            "credit_days": 7,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "ICICI Bank",
                "ifsc": "ICIC0002504",
                "account_number": "250401507636"
            }
        }
    },
    {
        "vendor_id": "55266bf1-b17b-410c-9c24-ea5e623377d9",
        "name": "Prodbrew Innovations LLP",
        "db_name": "Prodbrew Innovations LLP",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.02,
            "bank_details": {
                "bank": "HDFC",
                "ifsc": "HDFC0005675",
                "account_number": "50200113115397"
            }
        }
    },
    {
        "vendor_id": "4b211260-13d2-41d6-a5f8-6efb7ec80d5e",
        "name": "Rapti Gupta",
        "db_name": "Rapti Gupta",
        "set": {
            "credit_days": 7,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "ICICI Bank",
                "ifsc": "ICIC0000169",
                "account_number": "016901609025"
            }
        }
    },
    {
        "vendor_id": "631683a7-19d0-42f5-9b21-adcb68965f22",
        "name": "S SHANMUGA SUNTHARAM",
        "db_name": "S SHANMUGA SUNTHARAM",
        "set": {
            "credit_days": 7,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "ICICI Bank",
                "ifsc": "ICIC0000002",
                "account_number": "000201528213"
            }
        }
    },
    {
        "vendor_id": "62f3cb3e-279a-4f58-92e6-683044a85eb4",
        "name": "Sumo Technologies",
        "db_name": "Sumo Technologies",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.02,
            "bank_details": {
                "bank": "Indian Overseas Bank",
                "ifsc": "IOBA0000842",
                "account_number": "084202000000131"
            }
        }
    },
    {
        "vendor_id": "414054a3-8ee4-49ba-ae3c-ed4a872d6912",
        "name": "Swackit Digital Private Limited",
        "db_name": "Swackit Digital Private Limited",
        "set": {
            "credit_days": 7,
            "bank_details": {
                "bank": "Kotak Mahindra Bank",
                "ifsc": "KKBK0008040",
                "account_number": "6447195101"
            }
        }
    },
    {
        "vendor_id": "f05bd6c5-f808-49bf-9f58-96b9376441fe",
        "name": "Udit Dhariwal",
        "db_name": "Udit Dhariwal",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "DBS Bank",
                "ifsc": "DBSS0IN0811",
                "account_number": "881000610605"
            }
        }
    },
    {
        "vendor_id": "a19299c9-1d41-47cd-9f24-cfbea40c95c5",
        "name": "VEINTE MEDIA PRIVATE LIMITED",
        "db_name": "VEINTE MEDIA PRIVATE LIMITED",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC Bank",
                "ifsc": "HDFC0009173",
                "account_number": "50200034280416"
            }
        }
    },
    {
        "vendor_id": "d3a7130f-e791-4b09-9e3f-a28f6b585688",
        "name": "Vinayak Bansal",
        "db_name": "Vinayak Bansal",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC Bank",
                "ifsc": "HDFC0001388",
                "account_number": "50100723881009"
            }
        }
    },
    {
        "vendor_id": "691cb969-1204-48ed-a3a7-5ac9153157b0",
        "name": "Volition Digital Consulting Ltd",
        "db_name": "Volition Digital Consulting Ltd",
        "set": {
            "credit_days": 15
        }
    },
    {
        "vendor_id": "4fddc120-55fd-434c-aa2c-5e7aa407a91e",
        "name": "Webdart",
        "db_name": "Webdart",
        "set": {
            "credit_days": 7,
            "tds_rate": 0.02,
            "bank_details": {
                "bank": "HDFC Bank",
                "ifsc": "HDFC0000407",
                "account_number": "50200104305720"
            }
        }
    },
    {
        "vendor_id": "b2c7646e-4324-453d-91e3-fe6911c9ef13",
        "name": "Anil Kumar Rana",
        "db_name": "Anil Kumar Rana",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "UCO BANK",
                "ifsc": "UCBA0003478",
                "account_number": "02360110062524"
            }
        }
    },
    {
        "vendor_id": "4a8a683b-c7da-4d1d-981a-67b58299fbf8",
        "name": "Fiestaa Resort-n-Events Venue",
        "db_name": "Fiestaa Resort-n-events venue",
        "set": {
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "BANK OF BARODA",
                "ifsc": "BARB0VJCOXX",
                "account_number": "73800200000149"
            }
        }
    },
    {
        "vendor_id": "bb276fa1-582d-4c5e-8c7b-bb7b34432616",
        "name": "Beagle Cyber Innovations Pvt. Ltd.",
        "db_name": "Beagle Cyber Innovations Pvt. Ltd.",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "ICICI Bank",
                "ifsc": "ICIC0002534",
                "account_number": "033605007983"
            }
        }
    },
    {
        "vendor_id": "4e041e50-08b8-4783-a923-42d017081342",
        "name": "MIDP ED LABS LLP",
        "db_name": "MIDP ED LABS LLP",
        "set": {
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "AXIS BANK LTD",
                "ifsc": "UTIB0000232",
                "account_number": "925020025209015"
            }
        }
    },
    {
        "vendor_id": "b98fbc43-9d03-4373-b3ec-73a9ccaa7d50",
        "name": "Growth Natives Private Ltd",
        "db_name": "Growth Natives Private Ltd",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.02,
            "bank_details": {
                "bank": "IndusInd Bank",
                "ifsc": "INDB0000597",
                "account_number": "259779916756"
            }
        }
    },
    {
        "vendor_id": "8db56ed5-0826-4f9f-85f4-31f40c2bf3cd",
        "name": "DAIVAFORTUNE CONSULTING PRIVATE LIMITED",
        "db_name": "DAIVAFORTUNE CONSULTING PRIVATE LIMITED",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "ICICI BANK",
                "ifsc": "ICIC0008049",
                "account_number": "804905000033"
            }
        }
    },
    {
        "vendor_id": "6e1eeca1-9a68-42ab-94e2-9c64f5fdec40",
        "name": "PRESTIGE ESTATES PROJECTS LTD",
        "db_name": "PRESTIGE ESTATES PROJECTS LTD",
        "set": {
            "bank_details": {
                "bank": "Kotak Mahindra Bank",
                "ifsc": "KKBK0000422",
                "account_number": "8813227669"
            }
        }
    },
    {
        "vendor_id": "16197f5f-d7c2-4788-a792-033b9cc92291",
        "name": "Srikanth SK",
        "db_name": "Srikanth SK",
        "set": {
            "bank_details": {
                "bank": "Federal Bank",
                "ifsc": "FDRL0005555",
                "account_number": "55550111533657"
            }
        }
    },
    {
        "vendor_id": "4fddc120-55fd-434c-aa2c-5e7aa407a91e",
        "name": "GOPIKANNA",
        "db_name": "Webdart",
        "set": {
            "credit_days": 7,
            "tds_rate": 0.02,
            "bank_details": {
                "bank": "HDFC Bank",
                "ifsc": "HDFC0000407",
                "account_number": "5020010405720"
            }
        }
    },
    {
        "vendor_id": "ab27e644-dd4b-490f-976b-6453c244718b",
        "name": "Disha Brara",
        "db_name": "Disha Brara",
        "set": {
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC Bank",
                "ifsc": "HDFC0000280",
                "account_number": "50100042650227"
            }
        }
    },
    {
        "vendor_id": "3ee5ba39-6c88-4e12-99cd-29a2e922965b",
        "name": "AMYNTAS MEDIA WORKS LLP",
        "db_name": "AMYNTAS MEDIA WORKS LLP",
        "set": {
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "ICICI BANK",
                "ifsc": "ICIC0000021",
                "account_number": "2105026895"
            }
        }
    },
    {
        "vendor_id": "7f01a931-23ba-474c-b471-0a5e70ddedcd",
        "name": "Battalion Commerce",
        "db_name": "BATTALION COMMERCE",
        "set": {
            "credit_days": 15,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC Bank",
                "account_number": "50100723881009"
            }
        }
    },
    {
        "vendor_id": "2b25c6ed-98e3-4c98-9b5e-45ab07a56102",
        "name": "TREEBO HOSPITALITY VENTURES PVT LTD",
        "db_name": "TREEBO HOSPITALITY VENTURES PVT LTD",
        "set": {
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC Bank",
                "ifsc": "HDFC0000354",
                "account_number": "50200013010733"
            }
        }
    },
    {
        "vendor_id": "3828decf-6ac9-4caf-8a51-c3c997eda0b3",
        "name": "Silveroak Resort",
        "db_name": "Silveroak Resort",
        "set": {
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "Indus Bank",
                "ifsc": "INDB0002203",
                "account_number": "259740022228"
            }
        }
    },
    {
        "vendor_id": "831a7356-6e90-4385-b9bb-1ac202f95730",
        "name": "Visibility Gurus",
        "db_name": "Visibility Gurus",
        "set": {
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC Bank",
                "ifsc": "HDFC0001209",
                "account_number": "50200056463772"
            }
        }
    },
    {
        "vendor_id": "28ab3cea-5cc9-4f07-bade-e8cf9a3c3406",
        "name": "Xperforce Technologies LLP",
        "db_name": "Xperforce Technologies LLP",
        "set": {
            "credit_days": 30,
            "tds_rate": 0.1,
            "bank_details": {
                "bank": "HDFC BANK LTD",
                "ifsc": "HDFC0000077",
                "account_number": "50200014205487"
            }
        }
    }
]

apply_changes = "--apply" in sys.argv
client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=20000)
vendors = client["invoice_db"]["vendors"]

print(f"vendors in collection : {vendors.count_documents({})}")
print(f"updates prepared      : {len(UPDATES)}")
missing = [u for u in UPDATES if vendors.count_documents({"vendor_id": u["vendor_id"]}) == 0]
if missing:
    print(f"\n!! {len(missing)} vendor_id(s) not in the collection, will be skipped:")
    for u in missing: print(f"     {u['name']}")

if not apply_changes:
    print("\nDry run. Sample:")
    for u in UPDATES[:5]:
        print(f"   {u['name']}")
        for k, v in u["set"].items(): print(f"      {k} = {v}")
    print("\nRe-run with --apply to write it.")
    sys.exit(0)

result = vendors.bulk_write([UpdateOne({"vendor_id": u["vendor_id"]}, {"$set": u["set"]}) for u in UPDATES])
print(f"\nmatched {result.matched_count}, modified {result.modified_count}")
no_bank = list(vendors.find({"bank_details": {"$exists": False}}, {"vendor_name": 1, "_id": 0}))
print(f"\n{len(no_bank)} vendor(s) still have no bank details:")
for v in no_bank: print(f"   {v.get('vendor_name')}")
