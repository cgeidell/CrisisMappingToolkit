# -----------------------------------------------------------------------------
# Copyright * 2014, United States Government, as represented by the
# Administrator of the National Aeronautics and Space Administration. All
# rights reserved.
#
# The Crisis Mapping Toolkit (CMT) v1 platform is licensed under the Apache
# License, Version 2.0 (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0.
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.
# -----------------------------------------------------------------------------

import ee
import math

from cmt.domain import Domain
from cmt.modis.simple_modis_algorithms import *
from cmt.mapclient_qt import addToMap
from cmt.util.miscUtilities import safe_get_info
import cmt.modis.modis_utilities

"""
   Contains functions needed to implement an Adaboost algorithm using several of the
   simple MODIS classifiers.
"""


def _create_adaboost_learning_image(domain, b):
    '''Like _create_learning_image but using a lot of simple classifiers to feed into Adaboost'''
    
    #a = get_diff(b).select(['b1'], ['b1'])
    a = b['b1'].select(['sur_refl_b01'],                                                 ['b1'           ])
    a = a.addBands(b['b2'].select(['sur_refl_b02'],                                      ['b2'           ]))
    a = a.addBands(b['b2'].divide(b['b1']).select(['sur_refl_b02'],                      ['ratio'        ]))
    a = a.addBands(b['LSWI'].subtract(b['NDVI']).subtract(0.05).select(['sur_refl_b02'], ['LSWIminusNDVI']))
    a = a.addBands(b['LSWI'].subtract(b['EVI']).subtract(0.05).select(['sur_refl_b02'],  ['LSWIminusEVI' ]))
    a = a.addBands(b['EVI'].subtract(0.3).select(['sur_refl_b02'],                       ['EVI'          ]))
    a = a.addBands(b['LSWI'].select(['sur_refl_b02'],                                    ['LSWI'         ]))
    a = a.addBands(b['NDVI'].select(['sur_refl_b02'],                                    ['NDVI'         ]))
    a = a.addBands(b['NDWI'].select(['sur_refl_b01'],                                    ['NDWI'         ]))
    a = a.addBands(get_diff(b).select(['b1'],                                            ['diff'         ]))
    a = a.addBands(get_fai(b).select(['b1'],                                             ['fai'          ]))
    a = a.addBands(get_dartmouth(b).select(['b1'],                                       ['dartmouth'    ]))
    a = a.addBands(get_mod_ndwi(b).select(['b1'],                                        ['MNDWI'        ]))
    return a


def _find_adaboost_optimal_threshold(domains, images, truths, band_name, weights, splits):
    '''Binary search to find best threshold for this band'''
    
    EVAL_RESOLUTION = 250
    choices = []
    for i in range(len(splits) - 1):
        choices.append((splits[i] + splits[i+1]) / 2)
        
    domain_range = range(len(domains))
    best         = None
    best_value   = None
    for k in range(len(choices)):
        # Pick a threshold and count how many pixels fall under it across all the input images
        c = choices[k]
        errors = [safe_get_info(weights[i].multiply(images[i].select(band_name).lte(c).neq(truths[i])).reduceRegion(ee.Reducer.sum(), domains[i].bounds, EVAL_RESOLUTION))['constant'] for i in range(len(images))]
        error  = sum(errors)
        #threshold_sums = [safe_get_info(weights[i].mask(images[i].select(band_name).lte(c)).reduceRegion(ee.Reducer.sum(), domains[i].bounds, EVAL_RESOLUTION))['constant'] for i in domain_range]
        #flood_and_threshold_sum = sum(threshold_sums)
        #
        ##ts         = [truths[i].multiply(weights[i]).divide(flood_and_threshold_sum).mask(images[i].select(band_name).lte(c))              for i in domain_range]
        ##entropies1 = [-safe_get_info(ts[i].multiply(ts[i].log()).reduceRegion(ee.Reducer.sum(), domains[i].bounds, EVAL_RESOLUTION))['b1'] for i in domain_range]# H(Y | X <= c)
        ##ts         = [truths[i].multiply(weights[i]).divide(1 - flood_and_threshold_sum).mask(images[i].select(band_name).gt(c))           for i in domain_range]
        ##entropies2 = [-safe_get_info(ts[i].multiply(ts[i].log()).reduceRegion(ee.Reducer.sum(), domains[i].bounds, EVAL_RESOLUTION))['b1'] for i in domain_range]# H(Y | X > c)
        #
        ## Compute the sums of two entropy measures across all images
        #entropies1 = entropies2 = []
        #for i in domain_range:
        #    band_image     = images[i].select(band_name)
        #    weighted_truth = truths[i].multiply(weights[i])
        #    ts1            = weighted_truth.divide(    flood_and_threshold_sum).mask(band_image.lte(c)) # <= threshold
        #    ts2            = weighted_truth.divide(1 - flood_and_threshold_sum).mask(band_image.gt( c)) # >  threshold
        #    entropies1.append(-safe_get_info(ts1.multiply(ts1.log()).reduceRegion(ee.Reducer.sum(), domains[i].bounds, EVAL_RESOLUTION))['b1'])# H(Y | X <= c)
        #    entropies2.append(-safe_get_info(ts2.multiply(ts2.log()).reduceRegion(ee.Reducer.sum(), domains[i].bounds, EVAL_RESOLUTION))['b1'])# H(Y | X > c)
        #entropy1 = sum(entropies1)
        #entropy2 = sum(entropies2)
        #
        ## Compute the gain for this threshold choice
        #gain = (entropy1 * (    flood_and_threshold_sum)+
        #        entropy2 * (1 - flood_and_threshold_sum))
        #print 'c = %f, error = %f' % (c, error)
        if (best == None) or abs(0.5 - error) > abs(0.5 - best_value): # Record the maximum gain
            best       = k
            best_value = error
    
    # ??
    return (choices[best], best + 1, best_value)

def apply_classifier(image, band, threshold):
    '''Apply LTE threshold and convert to -1 / 1 (Adaboost requires this)'''
    return image.select(band).lte(threshold).multiply(2).subtract(1)

def get_adaboost_sum(domain, b, classifier = None):
    if classifier == None:
        # These are a set of known good computed values:  (Algorithm, Detection threshold, Weight)
        # learned from everything
        classifier = [(u'dartmouth', 0.30887438055782945, 1.4558371112080295), (u'b2', 2020.1975382568198, 0.9880130793929531), (u'MNDWI', 0.3677501330908955, 0.5140443440746121), (u'b2', 1430.1463073852296, 0.15367606716883875), (u'b1', 1108.5241042345276, 0.13193086117959033), (u'dartmouth', 0.7819758531686796, -0.13210548296374583), (u'dartmouth', 0.604427824270283, 0.12627962195951867), (u'b2', 1725.1719228210247, -0.07293616881105353), (u'b2', 1872.6847305389224, -0.09329031467870501), (u'b2', 1577.659115103127, 0.1182474134065663), (u'b2', 1946.441134397871, -0.13595282841411163), (u'b2', 2610.24876912841, 0.10010381165310277), (u'b2', 1983.3193363273454, -0.0934455057392682), (u'b2', 1503.9027112441784, 0.13483194249576771), (u'b2', 2001.7584372920826, -0.10099203054937314), (u'b2', 2905.2743845642053, 0.1135686859467779), (u'dartmouth', 0.5156538098210846, 0.07527677772747364), (u'b2', 2010.9779877744513, -0.09535260187161688), (u'b2', 1798.9283266799735, 0.07889358547222977), (u'dartmouth', 0.36787708796485785, -0.07370319016383906), (u'MNDWI', -0.6422574132273133, 0.06922934793487515), (u'dartmouth', 0.33837573426134365, -0.10266747186797487), (u'dartmouth', 0.4712668025964854, 0.09612545197834421), (u'dartmouth', 0.3236250574095866, -0.10754218805531587), (u'MNDWI', -0.48248013602276113, 0.111365639029263), (u'dartmouth', 0.316249718983708, -0.10620217821842894), (u'dartmouth', 0.4490732989841858, 0.09743861137429623), (u'dartmouth', 0.31256204977076874, -0.08121162639185005), (u'MNDWI', -0.5623687746250372, 0.10344420165347998), (u'dartmouth', 0.3107182151642991, -0.08899821447581886), (u'LSWI', -0.29661326544921773, 0.08652882218688322), (u'dartmouth', 0.3097962978610643, -0.07503568257204306), (u'MNDWI', 0.022523637136343283, 0.08765150582301148), (u'b2', 2015.5877630156356, -0.06978548014829108), (u'b2', 3052.7871922821028, 0.08567389991115743), (u'LSWI', -0.19275063787434812, 0.08357667312445341), (u'dartmouth', 0.3093353392094469, -0.08053950648462435), (u'LSWI', -0.14081932408691333, 0.07186342090261867), (u'dartmouth', 0.30910485988363817, -0.05720223719278896), (u'MNDWI', 0.19513688511361937, 0.07282637257701345), (u'NDWI', -0.361068160450533, 0.06565995208358431), (u'NDWI', -0.2074005503754442, -0.0522715989389411), (u'b1', 775.4361563517915, 0.05066415016422507), (u'b2', 2017.8926506362277, -0.0596357907686033), (u'b2', 1762.050124750499, 0.06600172638129476), (u'b2', 2019.0450944465238, -0.05498763067596745), (u'b1', 941.9801302931596, 0.06500771792028737), (u'dartmouth', 0.24987167315080105, 0.06409775979747406), (u'b2', 2979.0307884231543, 0.06178896578945445), (u'dartmouth', 0.22037031944728686, 0.04708770942378687), (u'dartmouth', 0.30898962022073384, -0.06357932266591948), (u'EVI', -0.13991172174597732, 0.061167901067941045), (u'dartmouth', 0.30893200038928165, -0.047538992866687814), (u'dartmouth', 0.23512099629904396, 0.055800430467148325), (u'dartmouth', 0.3089031904735555, -0.04993911823852714), (u'dartmouth', 0.22774565787316542, 0.045917043382747345), (u'b1', 232.32231270358304, -0.04624672841408699), (u'LSWIminusEVI', -1.3902019910129537, 0.044122210356250594), (u'fai', 914.8719936250361, 0.04696283008449494), (u'b2', 2019.6213163516718, -0.051114386132496435), (u'b2', 2315.2231536926147, 0.048898662215419296), (u'fai', 1434.706585047812, -0.05352547959475242), (u'diff', -544.4250000000001, -0.04459039609050114), (u'dartmouth', 0.39737844166837205, 0.045452678171318414), (u'dartmouth', 0.3088887855156925, -0.03891014191130265), (u'dartmouth', 0.22405798866022614, 0.042128457713671935), (u'diff', -777.2958333333333, -0.03902784979889064), (u'dartmouth', 0.2222141540537565, 0.03788131334473313), (u'dartmouth', 0.30888158303676094, -0.037208213701295255), (u'dartmouth', 0.3531264111131007, 0.0375648736301961), (u'dartmouth', 0.3088779817972952, -0.03427856593613819), (u'LSWI', -0.16678498098063071, 0.03430983541990538), (u'fai', -425.5957838307736, -0.03348006551810443), (u'NDWI', -0.13056674533789978, -0.03552899660957818), (u'b2', 2019.3332053990978, -0.0344936369203531), (u'b2', 1835.806528609448, 0.03856210900250611), (u'b2', 1467.0245093147041, -0.0345449746977328), (u'fai', 395.0374022022602, 0.031130251540884356), (u'fai', 654.9546979136481, 0.04214466417320743), (u'b2', 1448.5854083499669, -0.05667775728680656), (u'fai', 135.12010649087222, 0.03948338203848539), (u'dartmouth', 0.493460306208785, -0.045802615250103394), (u'fai', 784.9133457693422, 0.03128133499873274), (u'fai', 1174.7892893364242, -0.04413487095880613), (u'b2', 3015.9089903526283, 0.04133685218791008), (u'fai', 1304.7479371921181, -0.04107557606064173), (u'b2', 2462.7359614105126, 0.03777625735990945), (u'fai', 1369.727261119965, -0.03524600268462714), (u'b2', 2997.4698893878913, 0.03864830537283341), (u'dartmouth', 0.22313607135699132, 0.0348041704038284), (u'fai', -575.9950811359025, -0.036345846940478974), (u'fai', 1402.2169230838886, -0.03481517966048645), (u'fai', 719.9340218414952, 0.032833655233338276), (u'b2', 2019.1891499228109, -0.03272953788499046), (u'b2', 2388.9795575515636, 0.03713369823962704), (u'b2', 2019.1171221846673, -0.027949075715791222), (u'b2', 1743.611023785762, 0.03310357200312585), (u'LSWIminusNDVI', -0.3990346417915731, 0.029045726328998267), (u'NDWI', -0.16898364785667197, -0.025735337614573982), (u'dartmouth', 0.3088761811775623, -0.02973898070330325)]

        ## Trained only on Mississippi MODIS x 30
        #classifier = [(u'dartmouth', 0.4601187028446295, 1.3933244326017509), (u'b2', 2354.099683544304, 0.8009263433881945), (u'LSWIminusNDVI', -0.37403464179157314, -0.19660698864485893), (u'b2', 997.0490506329114, -0.22609471240379023), (u'LSWI', 0.7307270024497643, -0.19371590131103372), (u'b2', 2862.799841772152, 0.1958087739665682), (u'LSWI', 0.6519079187081369, -0.10411467161095657), (u'b2', 827.2238924050632, -0.1627002183497957), (u'LSWI', 0.6124983768373232, -0.1164763151056205), (u'b2', 3117.149920886076, 0.14045495336435712), (u'dartmouth', 0.8979830751809463, -0.12738474810727388), (u'b2', 1845.3995253164558, 0.09053474497663075), (u'LSWIminusNDVI', -0.4337695820901687, 0.0857764518831651), (u'dartmouth', 1.4135180226982098, 0.07075600803587892), (u'LSWIminusNDVI', 0.7774358230593219, -0.11418946709797562), (u'dartmouth', 1.3146504355818416, 0.10739211371940653), (u'dartmouth', 1.0568829618232098, -0.11015352163127118), (u'b2', 2989.974881329114, 0.08685181198332771), (u'LSWI', 0.8095460861913917, -0.08786494584616246), (u'b2', 912.1364715189873, -0.10303455995441295), (u'MNDWI', -0.29396969047077653, 0.07596098140939918), (u'dartmouth', 1.3640842291400257, 0.09266573131480345), (u'LSWI', 0.8489556280622054, -0.0783584759736119), (u'NDVI', 0.7748969641121193, 0.07406876644625034), (u'dartmouth', 0.9774330185020781, -0.08325113592851094), (u'dartmouth', 0.739083188538683, 0.07799308375292567), (u'dartmouth', 0.9377080468415122, -0.07468962616015257), (u'b2', 3053.562401107595, 0.08334191996627335), (u'LSWI', 0.8686603989976123, -0.05795058684040472), (u'b2', 954.5927610759493, -0.0764128019856413)]

        # Trained only on Bosnia data set
        # 30
        #classifier = [(u'fai', -105.57184246833049, 2.011175093865707), (u'diff', 1251.7560240963855, 0.8824534628772853), (u'LSWI', -0.1950396228838406, -0.34104836216017687), (u'MNDWI', 0.3156758628856189, 0.3971974262249763), (u'diff', 1652.1280120481929, 0.2301851751240506), (u'fai', 21.356797943057927, -0.25320140980424466), (u'ratio', 8.124320652173912, -0.27487135740578195), (u'MNDWI', 0.4338371139090058, 0.23183008491145138), (u'fai', -42.10752226263628, -0.2838952387654039), (u'ratio', 9.051290760869565, -0.18060837756218376), (u'MNDWI', 0.4929177394206993, 0.26151319350647706), (u'dartmouth', 0.7715202347815262, -0.19603377110432904), (u'MNDWI', 0.46337742666485254, 0.16945766306122212), (u'ratio', 9.51477581521739, -0.19326781484568353), (u'fai', -73.83968236548338, -0.1914520350524376), (u'dartmouth', 0.8985318397001062, 0.1320588534810869), (u'ratio', 9.746518342391305, -0.19297983361086213), (u'dartmouth', 0.9620376421593961, 0.138279854734148), (u'dartmouth', 0.8350260372408163, -0.11171574068467803), (u'fai', 84.82111814875213, 0.10168445603729025), (u'fai', -89.70576241690694, -0.14899335489218352), (u'NDVI', 0.7246944616336632, -0.12905505976725612), (u'MNDWI', 0.5224580521765461, 0.13393714338762214), (u'MNDWI', -0.09581370874173237, 0.0971904083985509), (u'fai', -97.63880244261871, -0.12420188150947246), (u'fai', 116.55327825159924, 0.1379596068377162), (u'fai', -101.6053224554746, -0.13973920004492527), (u'ratio', 9.862389605978262, -0.13788473854143857), (u'dartmouth', 0.9302847409297512, 0.12752031805931177), (u'dartmouth', 0.8032731360111712, -0.12615703892605462)]
        # 100
        #classifier = [(u'fai', -105.57184246833049, 2.011175093865707), (u'diff', 1251.7560240963855, 0.8824534628772853), (u'LSWI', -0.1950396228838406, -0.34104836216017687), (u'MNDWI', 0.3156758628856189, 0.3971974262249763), (u'diff', 1652.1280120481929, 0.2301851751240506), (u'fai', 21.356797943057927, -0.25320140980424466), (u'ratio', 8.124320652173912, -0.27487135740578195), (u'MNDWI', 0.4338371139090058, 0.23183008491145138), (u'fai', -42.10752226263628, -0.2838952387654039), (u'ratio', 9.051290760869565, -0.18060837756218376), (u'MNDWI', 0.4929177394206993, 0.26151319350647706), (u'dartmouth', 0.7715202347815262, -0.19603377110432904), (u'MNDWI', 0.46337742666485254, 0.16945766306122212), (u'ratio', 9.51477581521739, -0.19326781484568353), (u'fai', -73.83968236548338, -0.1914520350524376), (u'dartmouth', 0.8985318397001062, 0.1320588534810869), (u'ratio', 9.746518342391305, -0.19297983361086213), (u'dartmouth', 0.9620376421593961, 0.138279854734148), (u'dartmouth', 0.8350260372408163, -0.11171574068467803), (u'fai', 84.82111814875213, 0.10168445603729025), (u'fai', -89.70576241690694, -0.14899335489218352), (u'NDVI', 0.7246944616336632, -0.12905505976725612), (u'MNDWI', 0.5224580521765461, 0.13393714338762214), (u'MNDWI', -0.09581370874173237, 0.0971904083985509), (u'fai', -97.63880244261871, -0.12420188150947246), (u'fai', 116.55327825159924, 0.1379596068377162), (u'fai', -101.6053224554746, -0.13973920004492527), (u'ratio', 9.862389605978262, -0.13788473854143857), (u'dartmouth', 0.9302847409297512, 0.12752031805931177), (u'dartmouth', 0.8032731360111712, -0.12615703892605462), (u'fai', 53.08895804590503, 0.11280108368230035), (u'fai', -103.58858246190255, -0.1323621639708806), (u'fai', 68.95503809732858, 0.11111732544776452), (u'fai', -104.58021246511652, -0.09561460982792948), (u'ratio', 9.920325237771738, -0.11350588718525503), (u'MNDWI', 0.5372282085544694, 0.12457806680434337), (u'ratio', 9.949293053668477, -0.10142947278348549), (u'b1', 1024.4102564102564, 0.12769119462941034), (u'LSWIminusEVI', 1.2275397718573775, -0.10736324339508174), (u'b2', 3027.5, 0.11335627686056896), (u'LSWIminusEVI', 0.9969081432144813, -0.09855102339735189), (u'b2', 2856.25, 0.12341099685812779), (u'LSWIminusEVI', 1.1122239575359294, -0.09888830150850089), (u'fai', -105.0760274667235, -0.10867185036244718), (u'LSWI', -0.22884831003114736, 0.09390617010433257), (u'b1', 855.3653846153845, 0.09224267581066231), (u'dartmouth', 0.7873966853963488, -0.08654968485638263), (u'MNDWI', 0.5076878957986226, 0.08281065550953068), (u'LSWIminusEVI', 1.0545660503752052, -0.10070544168373859), (u'b1', 939.8878205128204, 0.09546509230524551), (u'LSWIminusEVI', 1.0257370967948432, -0.07512157753132792), (u'dartmouth', 0.9144082903149287, 0.08334614008782373), (u'MNDWI', 0.37475648839731235, -0.09347621062767539), (u'EVI', -0.5354007283235324, 0.07174011237019035), (u'MNDWI', 0.5298431303655078, 0.09325407788460913), (u'LSWIminusEVI', 1.011322620004662, -0.07094468512780058), (u'fai', -105.323934967527, -0.08699012823035202), (u'fai', 37.22287799448148, 0.0825373235212839), (u'dartmouth', 0.7794584600889375, -0.07138166975482693), (u'dartmouth', 0.8667789384704612, 0.07712045798967725), (u'dartmouth', 0.7754893474352318, -0.06762386155357324), (u'dartmouth', 0.6445086298629463, 0.07866161620559303), (u'fai', -105.44788871792875, -0.06872185637143167), (u'LSWI', -0.21194396645749397, 0.07899481836422567), (u'b1', 982.1490384615383, 0.067325499150263), (u'dartmouth', 0.773504791108379, -0.06463211254050916), (u'dartmouth', 0.5810028274036563, 0.07334871420010625), (u'b1', 770.8429487179487, -0.059061001597415036), (u'fai', -105.50986559312962, -0.0589202777929247), (u'EVI', -0.7131758497107877, 0.07543805485058235), (u'b2', 2941.875, 0.0769994468975548), (u'ratio', 9.934809145720108, -0.06559718164524614), (u'MNDWI', 0.5150729739875843, 0.0716339467915095), (u'dartmouth', 0.7725125129449526, -0.051293672818369905), (u'LSWI', -0.2034917946706673, 0.05938789552971963), (u'b1', 961.0184294871794, 0.07318340098162476), (u'b1', 813.1041666666666, -0.06572293073399683), (u'fai', 735.022130941929, 0.060554277275912824), (u'fai', 1028.3904772356705, -0.05029509963366562), (u'dartmouth', 0.9223465156223399, 0.0603999613021688), (u'ratio', 9.927567191745922, -0.059891901340974205), (u'fai', -105.54085403073006, -0.07731554985749788), (u'EVI', -0.62428828901716, 0.06719923296302845), (u'b1', 950.453125, 0.07579445528659902), (u'LSWIminusEVI', 1.0041153816095716, -0.056489892194208524), (u'MNDWI', 0.5113804348931035, 0.05913619958443161), (u'MNDWI', 0.4042968011531591, -0.05075066212771365), (u'diff', 44.518072289156635, 0.04720084087401721), (u'fai', -105.55634824953027, -0.05663241244401389), (u'diff', 247.76506024096386, 0.0590397873216613), (u'b2', 2899.0625, 0.05140907548559329), (u'NDVI', 0.678130801361386, -0.05420275867925114), (u'fai', -105.56409535893039, -0.0578072448545363), (u'NDVI', 0.6548489712252474, -0.07016682881460558), (u'dartmouth', 0.9183774029686342, 0.07612486244144435), (u'NDVI', 0.6664898862933167, -0.06024946186606819), (u'MNDWI', 0.544613286743431, 0.07203803995934993), (u'NDVI', 0.660669428759282, -0.055733385225458454), (u'b2', 2877.65625, 0.05993217671898275), (u'b1', 834.2347756410256, -0.05155918752640494)]
        
        # New Orleans 30
        #classifier = [(u'b2', 396.6109850939728, 1.4807909625958868), (u'b2', 1768.9536616979908, 0.8924003455619385), (u'b1', 854.8195364238411, 0.40691523803721014), (u'MNDWI', 0.3892345080908955, 0.36259940088550857), (u'dartmouth', 0.30135484930782946, -0.14337027100064728), (u'NDWI', -0.08016322692474476, -0.15831813233382272), (u'b2', 2281.2268308489956, 0.1353187435748396), (u'dartmouth', 0.7479193288987777, -0.16003727715630708), (u'b2', 2537.363415424498, 0.14061137727842032), (u'ratio', 6.880495612552257, -0.1166499595177218), (u'dartmouth', 0.3565977910898579, -0.11078288758401122), (u'NDWI', -0.2159195980788689, -0.10724023529180131), (u'dartmouth', 0.3289763201988437, -0.10755196279513543), (u'LSWI', -0.0569956547430914, 0.13659163207872768), (u'ratio', 3.9894934188283853, -0.10611849585590673), (u'b2', 2665.4317077122487, 0.14308264148123284), (u'ratio', 5.434994515690321, -0.0863607116606289), (u'b2', 2729.4658538561243, 0.08311291715506125), (u'fai', 902.9220972795897, -0.09359740396097607), (u'NDWI', -0.14804141250180683, -0.0705379771355723), (u'dartmouth', 0.31516558475333656, -0.08259143592340348), (u'LSWI', -0.14982826386693998, 0.08733446419322317), (u'ratio', 9.771497806276129, 0.08172740412577022), (u'MNDWI', -0.6315152257273133, 0.09657359625983933), (u'ratio', 11.216998903138064, 0.08246857226665376), (u'MNDWI', -0.7966635966818656, 0.07605162061421399), (u'dartmouth', 0.32207095247609013, -0.08295128868296117), (u'MNDWI', -0.7140894112045895, 0.0793663117501679), (u'ratio', 10.494248354707096, 0.08231229722793916), (u'LSWIminusEVI', -0.08557300521224676, -0.07043790156684442)]

        # 87 on a full set including a dozen lakes
        #classifier = [(u'dartmouth', 0.3191065854189406, 1.557305460141852), (u'MNDWI', 0.36596171757859164, 0.6348226054395288), (u'fai', 1076.7198220279101, 0.30760696551024047), (u'b1', 2490.1666666666665, 0.15428815637057783), (u'b1', 1382.4166666666665, 0.23468676605683622), (u'MNDWI', 0.016043270812331922, 0.2328762729873063), (u'diff', 1348.2627965043696, 0.0893530403812219), (u'EVI', -0.936229495395644, -0.0634313110230615), (u'EVI', 0.15713514272585227, -0.1369834357186273), (u'MNDWI', 0.19100249419546178, 0.1396065269707512), (u'EVI', -0.3895471763348959, -0.0699137042914175), (u'fai', -167.53021645595163, 0.09996436618217863), (u'diff', 3321.3055555555557, 0.09048885842380311), (u'fai', -39.46036556514488, 0.10447135022949844), (u'LSWIminusEVI', -1.8703796507388168, -0.08555612086933119), (u'fai', 24.57455988025849, 0.06788717248868892), (u'EVI', -0.1162060168045218, -0.07076437875624517), (u'EVI', 0.020464562960665234, -0.06640347420417587), (u'MNDWI', 0.2784821058870267, 0.0724098935614613), (u'LSWIminusEVI', -1.4401890608008658, -0.07070792766742959), (u'fai', -7.442902842443196, 0.07045138322018761), (u'EVI', -0.047870726921928286, -0.07285420746159146), (u'LSWIminusEVI', -1.2250937658318906, -0.055977386707896926), (u'b2', 3161.583333333333, -0.06589191057236488), (u'b2', 4305.708333333333, 0.04837026087353021), (u'dartmouth', 0.38322539525652455, 0.06306567258296356), (u'b2', 3733.645833333333, 0.054927931406532564), (u'dartmouth', 0.41528480017531655, 0.06032232647772757), (u'b2', 4019.677083333333, 0.0519316593408497), (u'dartmouth', 0.43131450263471255, 0.04868064475460096), (u'EVI', -0.013703081980631526, -0.052847995106752886), (u'b1', 828.5416666666666, -0.045046979840081554), (u'dartmouth', 0.4393293538644105, 0.03393621379393856), (u'b2', 436.6666666666667, -0.058719990230070525), (u'dartmouth', 0.44333677947925954, 0.055475163457599744), (u'dartmouth', 0.35116599033773255, -0.04464212550237975), (u'diff', 1956.9369538077403, -0.044786403818468996), (u'fai', 582.664653676786, 0.034362389553391215), (u'dartmouth', 0.3351362878783366, -0.03792028656513705), (u'dartmouth', 0.44534049228668404, 0.05187952065328861), (u'dartmouth', 0.3271214366486386, -0.05470657728868695), (u'MNDWI', -0.6666113750420029, 0.05405507219193603), (u'dartmouth', 0.32311401103378956, -0.05376528359478583), (u'dartmouth', 0.4463423486903963, 0.05449932480484019), (u'dartmouth', 0.32111029822636505, -0.0508089033370553), (u'MNDWI', -0.5002432754979653, 0.05120260867296932), (u'dartmouth', 0.32010844182265286, -0.0486732927468307), (u'dartmouth', 0.44684327689225245, 0.04692181887347917), (u'dartmouth', 0.31960751362079676, -0.04268244773234967), (u'MNDWI', -0.5834273252699841, 0.04712231236239887), (u'dartmouth', 0.31935704951986865, -0.04401637387406991), (u'MNDWI', -0.6250193501559935, 0.040914589219895145), (u'dartmouth', 0.31923181746940466, -0.038101469357921955), (u'dartmouth', 0.4470937409931805, 0.03911555294126862), (u'fai', 335.6370695012239, -0.0367701043425464), (u'fai', 212.12327741344288, -0.029512647597407196), (u'diff', 739.5886392009988, 0.04428176152799306), (u'diff', 1043.925717852684, 0.03722820575844798), (u'fai', 273.8801734573334, -0.04948130454705945), (u'dartmouth', 0.2549877755813566, 0.03377180068269043), (u'MNDWI', 0.3222219117328092, -0.04255121251198512), (u'diff', 1196.0942571785267, 0.045470427081376316), (u'MNDWI', 0.3440918146557004, -0.047085347000781985), (u'MNDWI', 0.23474230004124425, 0.04054075125347783), (u'MNDWI', 0.355026766117146, -0.046736584848805475), (u'MNDWI', 0.30035200880991797, 0.04078965556380944), (u'MNDWI', 0.3604942418478688, -0.04776704868198665), (u'LSWIminusNDVI', -0.5568084053554159, -0.04437083563150389), (u'MNDWI', 0.3112869602713636, 0.036248827530157374), (u'MNDWI', 0.36322797971323023, -0.04875479813967596), (u'MNDWI', 0.3167544360020864, 0.04123070622165848), (u'MNDWI', 0.36459484864591096, -0.03810930893128258), (u'diff', 1120.0099875156054, 0.035777913949894054), (u'fai', 829.6922378523481, -0.047446531409649384), (u'diff', 1081.9678526841449, 0.03507927976215228), (u'fai', 953.2060299401292, -0.04108410252021359), (u'b2', 4162.692708333333, 0.04836338373525389), (u'dartmouth', 0.44721897304364455, 0.03740129289331726), (u'dartmouth', 0.3191692014441726, -0.03601093736536919), (u'fai', 1014.9629259840196, -0.03601118264855319), (u'diff', 1158.052122347066, 0.04506611179539899), (u'fai', 1045.841374005965, -0.03593891978458228), (u'diff', 1652.599875156055, 0.03514766129091426), (u'fai', 1061.2805980169376, -0.034972506816040166), (u'fai', 1570.7749903790343, 0.03247104395760117), (u'MNDWI', -0.6042233377129889, 0.03328561186493601), (u'b2', 4091.184895833333, 0.03369074877033)]
        
    test_image = _create_adaboost_learning_image(domain, b)
    total = ee.Image(0).select(['constant'], ['b1'])
    for c in classifier:
      total = total.add(test_image.select(c[0]).lte(c[1]).multiply(2).subtract(1).multiply(c[2]))
    return total

def adaboost(domain, b, classifier = None):
    '''Run Adaboost classifier'''
    total = get_adaboost_sum(domain, b, classifier)
    return total.gte(-1.0) # Just threshold the results at zero (equal chance of flood / not flood)

def adaboost_dem(domain, b, classifier = None):
    
    # Get raw adaboost output
    total = get_adaboost_sum(domain, b, classifier)
    #addToMap(total, {'min': -10, 'max': 10}, 'raw ADA', False)
    
    # Convert this range of values into a zero to one probability scale
    #MIN_SUM = -3.5 # These numbers are a pretty good probability conversion, but it turns out
    #MAX_SUM =  1.0 #  that probability does not make a good input to apply_dem().
    
    MIN_SUM = -2.0 # These numbers are tuned to get better results
    MAX_SUM =  0.5
    
    val_range = MAX_SUM - MIN_SUM
    
    fraction = total.subtract(ee.Image(MIN_SUM)).divide(ee.Image(val_range)).clamp(0.0, 1.0)
    #addToMap(fraction, {'min': 0, 'max': 1}, 'fraction', False)
    return cmt.modis.modis_utilities.apply_dem(domain, fraction, True)



def __compute_threshold_ranges(training_domains, training_images, water_masks, bands):
    '''For each band, find lowest and highest fixed percentiles among the training domains.'''
    LOW_PERCENTILE  = 20
    HIGH_PERCENTILE = 100
    EVAL_RESOLUTION = 250
    
    band_splits = dict()
    for band_name in bands: # Loop through each band (weak classifier input)
        split = None
        print 'Computing threshold ranges for: ' + band_name
      
        mean = 0
        for i in range(len(training_domains)): # Loop through all input domains
            # Compute the low and high percentiles for the data in the training image
            masked_input_band = training_images[i].select(band_name).mask(water_masks[i])
            ret = safe_get_info(masked_input_band.reduceRegion(ee.Reducer.percentile([LOW_PERCENTILE, HIGH_PERCENTILE], ['s', 'b']), training_domains[i].bounds, EVAL_RESOLUTION))
            s   = [ret[band_name + '_s'], ret[band_name + '_b']] # Extract the two output values
            mean += modis_utilities.compute_binary_threshold(training_images[i].select([band_name], ['b1']), water_masks[i], training_domains[i].bounds)
            
            if split == None: # True for the first training domain
                split = s
            else: # Track the minimum and maximum percentiles for this band
                split[0] = min(split[0], s[0])
                split[1] = max(split[1], s[1])
        mean = mean / len(training_domains)
            
        # For this band: bound by lowest percentile and maximum percentile, start by evaluating mean
        band_splits[band_name] = [split[0], split[0] + (mean - split[0]) / 2, mean + (split[1] - mean) / 2, split[1]]
    return band_splits

def adaboost_learn():
    '''Train Adaboost classifier'''
    
    EVAL_RESOLUTION = 250

    # Learn this many weak classifiers
    NUM_CLASSIFIERS_TO_TRAIN = 100

    # Load inputs for this domain and preprocess
    # - Kashmore does not have a good unflooded comparison location so it is left out of the training.
    #all_problems      = ['kashmore_2010_8.xml', 'mississippi_2011_5.xml', 'mississippi_2011_6.xml', 'new_orleans_2005_9.xml', 'sf_bay_area_2011_4.xml']
    #all_domains       = [Domain('config/domains/modis/' + d) for d in all_problems]
    #training_domains  = [domain.unflooded_domain for domain in all_domains[:-1]] + [all_domains[-1]] # SF is unflooded
    
    all_problems      = ['unflooded_mississippi_2010.xml', 'unflooded_new_orleans_2004.xml', 'sf_bay_area_2011_4.xml', 'unflooded_bosnia_2013.xml']
    all_domains       = [Domain('config/domains/modis/' + d) for d in all_problems]
    
    # Add a bunch of floods to the training data
    lake_problems = ['Amistad_Reservoir/Amistad_Reservoir_2014-07-01_train.xml',
                     'Cascade_Reservoir/Cascade_Reservoir_2014-09-01_train.xml',
                     'Edmund/Edmund_2014-07-01_train.xml',
                     'Hulun/Hulun_2014-07-01_train.xml',
                     'Keeley/Keeley_2014-06-01_train.xml',
                     'Lake_Mead/Lake_Mead_2014-09-01_train.xml',
                     'Miguel_Aleman/Miguel_Aleman_2014-08-01_train.xml',
                     'Oneida_Lake/Oneida_Lake_2014-06-01_train.xml',
                     'Quesnel/Quesnel_2014-08-01_train.xml',
                     'Shuswap/Shuswap_2014-08-01_train.xml',
                     'Trikhonis/Trikhonis_2014-07-01_train.xml',
                     'Pickwick_Lake/Pickwick_Lake_2014-07-01_train.xml',
                     'Rogoaguado/Rogoaguado_2014-08-01_train.xml',
                     'Zapatosa/Zapatosa_2014-09-01_train.xml']
    lake_domains  = [Domain('/home/smcmich1/data/Floods/lakeStudy/' + d) for d in lake_problems]
    all_problems += lake_problems
    all_domains  += lake_domains
    
    #all_problems      = ['unflooded_mississippi_2010.xml']
    #all_domains       = [Domain('config/domains/modis/' + d) for d in all_problems]

    #all_problems      = ['sf_bay_area_2011_4.xml']
    #all_domains       = [Domain('config/domains/modis/' + d) for d in all_problems]
    #
    #all_problems      = ['unflooded_bosnia_2013.xml']
    #all_domains       = [Domain('config/domains/modis/' + d) for d in all_problems]
    #
    #all_problems      = ['unflooded_new_orleans_2004.xml']
    #all_domains       = [Domain('config/domains/modis/' + d) for d in all_problems]
    
    training_domains  = all_domains
    
    water_masks       = [modis_utilities.get_permanent_water_mask() for d in training_domains]
    training_images   = [_create_adaboost_learning_image(d, modis_utilities.compute_modis_indices(d)) for d in training_domains]
    
    # add pixels in flood permanent water masks to training
    #training_domains.extend(all_domains)
    #water_masks.extend([get_permanent_water_mask() for d in all_domains])
    #training_images.append([_create_adaboost_learning_image(domain, compute_modis_indices(domain)).mask(get_permanent_water_mask()) for domain in all_domains])
    
    transformed_masks = [water_mask.multiply(2).subtract(1) for water_mask in water_masks]

    bands             = safe_get_info(training_images[0].bandNames())
    print 'Computing threshold ranges.'
    band_splits = __compute_threshold_ranges(training_domains, training_images, water_masks, bands)
    counts = [safe_get_info(training_images[i].select('diff').reduceRegion(ee.Reducer.count(), training_domains[i].bounds, 250))['diff'] for i in range(len(training_images))]
    count = sum(counts)
    weights = [ee.Image(1.0 / count) for i in training_images] # Each input pixel in the training images has an equal weight
    
    # Initialize for pre-existing partially trained classifier
    full_classifier = []
    for (c, t, alpha) in full_classifier:
        band_splits[c].append(t)
        band_splits[c] = sorted(band_splits[c])
        total = 0
        for i in range(len(training_images)):
            weights[i] = weights[i].multiply(apply_classifier(training_images[i], c, t).multiply(transformed_masks[i]).multiply(-alpha).exp())
            total += safe_get_info(weights[i].reduceRegion(ee.Reducer.sum(), training_domains[i].bounds, EVAL_RESOLUTION))['constant']
        for i in range(len(training_images)):
            weights[i] = weights[i].divide(total)
    
    ## Apply weak classifiers to the input test image
    #test_image = _create_adaboost_learning_image(domain, b)
    
    
    while len(full_classifier) < NUM_CLASSIFIERS_TO_TRAIN:
        best = None
        for band_name in bands: # For each weak classifier
            # Find the best threshold that we can choose
            (threshold, ind, error) = _find_adaboost_optimal_threshold(training_domains, training_images, water_masks, band_name, weights, band_splits[band_name])
            
            # Compute the sum of weighted classification errors across all of the training domains using this threshold
            #errors = [safe_get_info(weights[i].multiply(training_images[i].select(band_name).lte(threshold).neq(water_masks[i])).reduceRegion(ee.Reducer.sum(), training_domains[i].bounds, EVAL_RESOLUTION))['constant'] for i in range(len(training_images))]
            #error  = sum(errors)
            print '%s found threshold %g with error %g' % (band_name, threshold, error)
            
            # Record the band/threshold combination with the highest abs(error)
            if (best == None) or (abs(0.5 - error) > abs(0.5 - best[0])): # Classifiers that are always wrong are also good with negative alpha
                best = (error, band_name, threshold, ind)
        
        # add an additional split point to search between for thresholds
        band_splits[best[1]].insert(best[3], best[2])
      
        print '---> Using %s < %g. Error %g.' % (best[1], best[2], best[0])
        alpha      = 0.5 * math.log((1 - best[0]) / best[0])
        classifier = (best[1], best[2], alpha)
        full_classifier.append(classifier)
        print '---> Now have %d out of %d classifiers.' % (len(full_classifier), NUM_CLASSIFIERS_TO_TRAIN)
        
        # update the weights
        weights = [weights[i].multiply(apply_classifier(training_images[i], classifier[0], classifier[1]).multiply(transformed_masks[i]).multiply(-alpha).exp()) for i in range(len(training_images))]
        totals  = [safe_get_info(weights[i].reduceRegion(ee.Reducer.sum(), training_domains[i].bounds, EVAL_RESOLUTION))['constant'] for i in range(len(training_images))]
        total   = sum(totals)
        weights = [w.divide(total) for w in weights]
        print full_classifier

#
#import modis_utilities
#import pickle
#def adaboost_dem_learn(classifier = None):
#    '''Train Adaboost classifier'''
#    
#    EVAL_RESOLUTION = 250
#
#    # Load inputs for this domain and preprocess
#    #all_problems      = ['kashmore_2010_8.xml', 'mississippi_2011_5.xml', 'mississippi_2011_6.xml', 'new_orleans_2005_9.xml', 'sf_bay_area_2011_4.xml']
#    all_problems      = ['mississippi_2011_6.xml', 'new_orleans_2005_9.xml', 'sf_bay_area_2011_4.xml']
#    all_domains       = [Domain('config/domains/modis/' + d) for d in all_problems]
#    training_domains  = [domain.unflooded_domain for domain in all_domains[:-1]] + [all_domains[-1]] # SF is unflooded
#    water_masks       = [modis_utilities.get_permanent_water_mask() for d in training_domains]
#    
#    THRESHOLD_INTERVAL =  0.5
#    MIN_THRESHOLD      = -5.0
#    MAX_THRESHOLD      =  2.0
#    
#    print 'Computing thresholds'
#    
#    results = []
#        
#    # Loop through each of the raw result images
#    for (truth_image, train_domain, name) in zip(water_masks, training_domains, all_problems):
#
#        truth_image = truth_image.mask(ee.Image(1))
#
#        # Apply the Adaboost computation to each training image and get the raw results
#        b = modis_utilities.compute_modis_indices(train_domain)
#        sum_image = get_adaboost_sum(train_domain, b, classifier)
#        #addToMap(sum_image, {'min': -10, 'max': 10}, 'raw ADA', False)
#        #addToMap(truth_image, {'min': 0, 'max': 1}, 'truth', False)
#        print '================================'
#        print name
#
#        #pickle.dump( truth_image, open( "truth.pickle", "wb" ) )
#        
#        results_list = []
#        
#        # For each threshold level above zero, how likely is the pixel to be actually flooded?
#        curr_threshold = MIN_THRESHOLD
#        percentage = 0
#        #while percentage < TARGET_PERCENTAGE:
#        while curr_threshold <= MAX_THRESHOLD:
#            
#            curr_results = sum_image.gte(curr_threshold)
#            
#            curr_results = curr_results.mask(ee.Image(1))
#            
#            #addToMap(curr_results, {'min': 0, 'max': 1}, str(curr_threshold), False)
#            
#            #addToMap(curr_results.multiply(truth_image), {'min': 0, 'max': 1}, 'mult', False)
#            
#            sum_correct  = safe_get_info(curr_results.multiply(truth_image).reduceRegion(ee.Reducer.sum(), train_domain.bounds, EVAL_RESOLUTION, 'EPSG:4326'))['b1']
#            sum_total    = safe_get_info(curr_results.reduceRegion(ee.Reducer.sum(), train_domain.bounds, EVAL_RESOLUTION, 'EPSG:4326'))['b1']
#            #print sum_correct
#            if sum_total > 0:
#                percentage   = sum_correct / sum_total
#                print str(curr_threshold) +': '+ str(sum_total) + ' --> '+ str(percentage)
#                results_list.append(percentage)
#                #pickle.dump( curr_results, open( "detect.pickle", "wb" ) )
#            else: # Time to break out of the loop
#                results.append(results_list)
#                break
#            curr_threshold += THRESHOLD_INTERVAL
#        else:
#            results.append(results_list)
#        
#    logFile = open('adaboostProbabilityLog.txt', 'w')
#    logFile.write('Threshold, Miss_5, Miss_6, NO, SF\n')
#    for r in range(15):
#        logFile.write(str(r*THRESHOLD_INTERVAL + MIN_THRESHOLD))
#        for i in range(3):
#            logFile.write(str(results[i][r]) + ', ')
#        logFile.write(str(results[3][r]) + '\n')
#    logFile.close()
#        
#        
#        #raise Exception('DEBUG')
#
##        # For each threshold level below zero, how likely is the pixel to be actually dry?
        



