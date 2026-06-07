/**
 * Canonical JA2 1.13 nationality table: bNationality index -> display label.
 *
 * This is a verbatim mirror of the engine's own string array so the dropdown
 * can never drift from what the game shows. DO NOT hand-edit individual rows —
 * if 1.13 ever adds nationalities, regenerate from the two engine sources:
 *
 *   - enum Nationalities        Tactical/soldier profile type.h
 *       (AMERICAN_NAT = 0 ... ZIMBABWEAN_NAT = 112, NUM_NATIONALITIES = 113)
 *   - labels szNationalityText[]  i18n/_EnglishText.cpp (English build)
 *
 * Index 9 (Traconian) is the UB/Tracona filler the engine added so the enum
 * has no holes. Omitting it was the off-by-one that made Barry (bNationality
 * = 16 = Hungarian) render as "Scottish" (17) in the beta — every nationality
 * from 9 up was shifted one slot too low.
 *
 * The engine read path clamps bNationality to [0, NUM_NATIONALITIES - 1]
 * (Tactical/XML_Profiles.cpp), so values outside 0..112 only occur on
 * mod-extended installs; consumers surface those via a raw "(custom: N)" entry.
 *
 * Labels mirror the engine's English spelling exactly — including its quirks
 * (Brasilian, Columbian, Islandic, Lybian, Myanma, Portoguese, Rwandanese) —
 * so the wizard matches in-game text instead of inventing its own. If prettier
 * spellings are wanted, add a display-override map rather than editing the
 * canonical rows here.
 */
export const NATIONALITY_OPTIONS: ReadonlyArray<readonly [number, string]> = [
  [0, "American"], [1, "Arab"], [2, "Australian"], [3, "British"], [4, "Canadian"],
  [5, "Cuban"], [6, "Danish"], [7, "French"], [8, "Russian"], [9, "Traconian"],
  [10, "Swiss"], [11, "Jamaican"], [12, "Polish"], [13, "Chinese"], [14, "Irish"],
  [15, "South African"], [16, "Hungarian"], [17, "Scottish"], [18, "Arulcan"], [19, "German"],
  [20, "African"], [21, "Italian"], [22, "Dutch"], [23, "Romanian"], [24, "Metaviran"],
  [25, "Afghan"], [26, "Albanian"], [27, "Argentinian"], [28, "Armenian"], [29, "Azerbaijani"],
  [30, "Bangladeshi"], [31, "Belarusian"], [32, "Belgian"], [33, "Beninese"], [34, "Bolivian"],
  [35, "Bosnian"], [36, "Brasilian"], [37, "Bulgarian"], [38, "Cambodian"], [39, "Chadian"],
  [40, "Chilean"], [41, "Columbian"], [42, "Congolese"], [43, "Croatian"], [44, "Ecuadorian"],
  [45, "Egyptian"], [46, "English"], [47, "Eritrean"], [48, "Estonian"], [49, "Ethiopian"],
  [50, "Filipino"], [51, "Finnish"], [52, "Georgian"], [53, "Greek"], [54, "Guatemalan"],
  [55, "Haitian"], [56, "Honduran"], [57, "Indian"], [58, "Indonesian"], [59, "Iranian"],
  [60, "Iraqi"], [61, "Islandic"], [62, "Israeli"], [63, "Japanese"], [64, "Jordanian"],
  [65, "Kazakhstani"], [66, "Korean"], [67, "Kyrgyzstani"], [68, "Laotian"], [69, "Latvian"],
  [70, "Lebanese"], [71, "Lithuanian"], [72, "Lybian"], [73, "Macedonian"], [74, "Malaysian"],
  [75, "Mexican"], [76, "Mongolian"], [77, "Moroccan"], [78, "Mozambican"], [79, "Myanma"],
  [80, "Namibian"], [81, "Nicaraguan"], [82, "Nigerian"], [83, "Nigerien"], [84, "Norwegian"],
  [85, "Pakistani"], [86, "Panamanian"], [87, "Portoguese"], [88, "Rwandanese"], [89, "Salvadoran"],
  [90, "Saudi"], [91, "Serbian"], [92, "Slovakian"], [93, "Slovenian"], [94, "Somali"],
  [95, "Spanish"], [96, "Sudanese"], [97, "Swedish"], [98, "Syrian"], [99, "Thai"],
  [100, "Togolese"], [101, "Tunisian"], [102, "Turkish"], [103, "Ugandan"], [104, "Ukrainian"],
  [105, "Uruguayan"], [106, "Uzbekistani"], [107, "Venezuelan"], [108, "Vietnamese"], [109, "Welsh"],
  [110, "Yemeni"], [111, "Zamundan"], [112, "Zimbabwean"],
];
