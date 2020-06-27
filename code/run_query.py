#!/usr/bin/env python
# coding: utf-8

#from geofeather.pygeos import to_geofeather, from_geofeather
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points
import pandas as pd
import geopandas as gpd
import glob, json, pprint, pygeos, random, sys, time

pd.options.mode.chained_assignment = None  # default='warn'
gpd.options.use_pygeos = True

###############
### HELPERS ###
###############

# def read_data(path_to_tracts, path_to_shp):
    
#     dtype = { 
        
#         "CD_GEOCODI": str,
#         "CD_GEOCODM": str,
#         "CD_MUNICIP": str,
#         "Cod_setor": str
        
#     }
    
#     tracts = pd.read_csv(path_to_tracts, dtype=dtype)
    
#     shp = gpd.read_file(path_to_shp, dtype=dtype)
    
#     return tracts, shp

# def random_points(n=1):
    
#     '''
#     Generates n random points in the country
#     '''
    
#     # Load the outline of Brazil
#     polygon = gpd.read_file("../data/malha_brasil/malha.json")
    
#     polygon = polygon.loc[0, 'geometry']
        
#     # Get's its bounding box
#     minx, miny, maxx, maxy = polygon.bounds
    
#     # Generates a random point within the bounding box
#     # untill it falls within the country
    
#     points = [ ]
    
#     while len(points) < n:
        
#         point = Point(random.uniform(minx, maxx), random.uniform(miny, maxy))
        
#         if polygon.contains(point):
            
#             points.append(point)
            
#     return points

def parse_input(argv):
    
    '''
    Parses the input that was passad 
    through the command line and returns
    a dictionary_object
    '''
                
    point = [float(coord.strip()) for coord in argv]
    
    point = Point(point[1], point[0]) # Shapely requires a lon, lat point

    return point

def get_covid_count(measure='deaths'):
    '''    
    Returns the current number of covid deaths
    or cases registered in the country according
    to that tha we have pre-processed
    '''
    
    with open("../output/case_count.json") as file:

        data = json.load(file)

    return data[measure]

def find_user_area(point, target):
    
    '''
    Finds the area that we will need to
    process according to the position of the point

    TO DO: use Pandas vectorization optimization instead of iterating through rows
    '''
    
    # A list with the quadrants whose population should be counted
    
    quadrants_to_count = [ ]
    
    # A list that will be filled with the ids of the quadrants that we need to load
    # That is, the quadrants to count plust its neighbors
    
    quadrants_to_load = [ ]
    
    # Loads the quadrant data
    
    reference_map = gpd.read_feather("../output/index_tracts_bboxes.feather")
       
    # Finds in which quadrant the point falls
    
    user_area = reference_map[ reference_map.geometry.contains(point) ].reset_index(drop=True)
        
    assert user_area.shape[0] == 1
        
    # At first, we will count the population in that particular quadrant
        
    quadrants_to_count.append(user_area.loc[0, 'id_no'])
    
    # And add itself and its neighbors to those we should load
    quadrants_to_load.append(user_area.loc[0, 'id_no'])
    
    quadrants_to_load.extend(user_area.loc[0, 'neighbors'].split("|"))

    # Checks if the population is enough. If not, add more quadrants
        
    population_in_area = reference_map[ reference_map.id_no.isin(quadrants_to_count)].total_population.sum()
    
    while population_in_area < target:
                                 
        # Adds the neighbors of all counted quadrants to those we should count
        
        for index, row in reference_map[ reference_map.id_no.isin(quadrants_to_count)].iterrows():
                
            quadrants_to_count.extend(row.neighbors.split("|"))
                                      
        quadrants_to_count = list(set(quadrants_to_count))
                                    
        
        # Adds the neighbors of the loaded neighbors to those we should load
        
        for index, row in reference_map[ reference_map.id_no.isin(quadrants_to_load)].iterrows():
                                     
            quadrants_to_load.extend(row.neighbors.split("|"))
                                    
        quadrants_to_load = list(set(quadrants_to_load))
        
        # Gets the new population
        
        population_in_area = reference_map[ reference_map.id_no.isin(quadrants_to_count)].total_population.sum()
    
    # Loads the data in
    
    gdfs = [ ]
    
    quadrants = reference_map [reference_map.id_no.astype(str).isin(quadrants_to_load) ]
    
    for index, row in quadrants.iterrows():
        
        fpath = row.fpath
        
        gdf = gpd.read_feather(fpath)
        
        gdfs.append(gdf)
        
    return pd.concat(gdfs)

def find_user_city(point, target):
    '''
    Finds and loads the bounding box which contains
    the user city and retrieves its data
    '''

    # Loads the quadrant data
    
    reference_map = gpd.read_feather("../output/index_city_bboxes.feather")
       
    # Finds in which quadrant the point falls
    
    quadrant = reference_map[ reference_map.geometry.contains(point) ].reset_index(drop=True)

    assert quadrant.shape[0] == 1

    quadrant = gpd.read_feather(quadrant.loc[0, "fpath"])

    # Find in which city of the quadrant the point falls in

    user_city = quadrant[ quadrant.geometry.contains(point) ].reset_index(drop=True)

    assert user_city.shape[0] == 1

    city_data = {

        "code_muni": user_city.loc[0, "code_muni"],
        "name_muni": user_city.loc[0, "name_muni"],
        "name_state": user_city.loc[0, "name_state"],
        "pop_2019": user_city.loc[0, "pop_2019"],
        "city_centroid": user_city.loc[0, "geometry"].centroid.coords[0],
        "would_vanish": True if (user_city.loc[0, "pop_2019"] <= target) else False

    }

    return city_data

def merge_tracts_and_shape(tracts, shp):
    
    return shp.merge(tracts, left_on='CD_GEOCODI', right_on='Cod_setor', how='left')

def find_radius(point, tracts, spatial_index, target):
    
    ########################
    ### HELPER FUNCTIONS ###
    ########################
    
    def find_intersections(tracts, spatial_index, area):
        '''
        Finds all the polygons that intersect a given radius
        '''
        
        # Uses Geopandas/PyGeos rtree to pre-filter the tracts
        nearby_index = list(spatial_index.intersection(area.bounds))
        
        nearby_tracts = tracts.iloc[nearby_index]
        
        # Selects the tracts that do intersect with the area
        matches = nearby_tracts [ nearby_tracts.geometry.intersects(area)]
        
        return matches
        
                    
    def compute_population_in_area(matches, area):
        '''
        Calculates how many people live in the intersecting polygons.
        Also returns an array with the intersecting shapes.
        '''

        def process_intersection(population, tract, polygon):

            intersection = tract.intersection(polygon)

            intersection_percentage = intersection.area / tract.area 

            population_in_intersection = population * intersection_percentage

            return intersection, population_in_intersection

        intersection, population_in_intersection = process_intersection(matches.populacao_residente.values,
                                         matches.geometry.values,
                                         area)

        matches['geometry'] = intersection
        
        matches['population_in_intersection'] = population_in_intersection

        return matches
    
    #################
    ### EXECUTION ###
    #################
    
    checkpoint = time.time()
    
    total_people = 0
    
    radius = .01 # This unit is lat/lon degrees
    
    checkpoint_b = time.time()
    
    # While we don't meet the population target, we keep increasing the radius to grab more people
    while True:
                
        total_people = 0
        
        area = point.buffer(radius)
        
        matches = find_intersections(tracts, spatial_index, area)
        
        matches = compute_population_in_area(matches, area)
        
        total_people = round(matches.population_in_intersection.sum())
                        
        if total_people < target:
        
            radius = radius * 1.5
            
            continue
        
        # Else, finish the iteration
        else:
            
            break
            
            
    # Now we can move into the fine-tuning, removing excess population
    
    checkpoint_b = time.time()
    
    direction = 'shrink'
    
    fine_tune = .5
    
    max_tolerance = target * 1.1
    
    min_tolerance = target * 0.9
    
    while True:
                        
        if total_people > max_tolerance:
            
            new_direction = 'shrink'
    
            total_people = 0
            
            radius = radius * (1 - fine_tune)
            
            area = point.buffer(radius)
            
            matches = find_intersections(tracts, spatial_index, area)
        
            matches = compute_population_in_area(matches, area)

            total_people = round(matches.population_in_intersection.sum())
                        
            if total_people <= max_tolerance and total_people >= min_tolerance:
                
                break
                
        
        elif total_people < min_tolerance:
            
            new_direction = 'grow'
            
            total_people = 0
            
            radius = radius * (1 + fine_tune)
            
            area = point.buffer(radius)
            
            matches = find_intersections(tracts, spatial_index, area)
        
            matches = compute_population_in_area(matches, area)

            total_people = round(matches.population_in_intersection.sum())
            
            
            if total_people <= max_tolerance and total_people >= min_tolerance:
                
                break
                
        else: # It's equal
            
            break
                
        if new_direction != direction:
                        
            direction = new_direction
    
            fine_tune = fine_tune / 2
     
        
    matches = matches[["CD_GEOCODI", "geometry", "population_in_intersection"]]
    
    # return matches, area

    #matches.to_feather(f"../output/radiuses/{point}.feather")
    
    radius_data = {

        "inner_point": point.coords[0],
        "outer_point": area.exterior.coords[0]

    }

    return radius_data

def find_neighboring_city(point, target):

    '''
    Returns the city with less population than covid-cases
    that is nearest to the user input point
    '''

    city_centroids = gpd.read_feather("../output/city_centroids.feather")

    city_centroids = city_centroids [ city_centroids.pop_2019 <= target ]

    multipoint = city_centroids.unary_union

    source, nearest = nearest_points(point, multipoint)

    nearest = city_centroids [ city_centroids.geometry == nearest ].reset_index(drop=True)

    assert nearest.shape[0] == 1

    neighbor_data = {

        "code_muni": nearest.loc[0, "code_muni"],
        "name_muni": nearest.loc[0, "name_muni"],
        "name_state": nearest.loc[0, "name_state"],
        "pop_2019": nearest.loc[0, "pop_2019"],
        "city_centroid": nearest.loc[0, "geometry"].coords[0]

    }

    return neighbor_data

def choose_capitals(user_city_id):
    '''
    Randomly selects two state capitals to highlight.
    Makes sure its not the user city.
    '''

    with open("../output/capitals_radius.json") as file:

        capitals_data = json.load(file)

    capitals_data = [ item for item in capitals_data if item["code_muni"] != user_city_id ]

    capitals_data = random.sample(capitals_data, 2)

    return capitals_data

###############
### WRAPPER ###
###############

def run_query(point):

    '''
    Point is an array of two strings
    representing the lat and lon
    coordinates of a point in space
    '''
    
    # Gets information from the user input
    point = parse_input(point)

    # Opens the file with the current count of covid-19 deaths
    target = get_covid_count(measure='deaths')
 
    # Gets the parts of the census tracts with the user data that we need to load
    gdf = find_user_area(point, target)
        
    # Uses a buffer to avoid self-intercepting shapes
    gdf["geometry"] = gdf.geometry.buffer(0)
        
    # Creates a sindex to improve search
    spatial_index = gdf.sindex
        
    # Finds the area that we will need to highlight along with the respective population
    radius_data = find_radius(point, gdf, spatial_index, target)

    # Finds informations about the user city
    city_data = find_user_city(point, target)

    # Finds the closest city with population similar to the total deaths
    neighbor_data = find_neighboring_city(point, target)

    # Selects two random capitals to highlight
    capitals_data = choose_capitals(city_data["code_muni"])

    output = {

        "radius": radius_data,

        "user_city": city_data,

        "neighboring_city": neighbor_data,

        "capitals_to_highlight": capitals_data

    }

    #pprint.pprint(output)

    return output

    # print(radius_data)

    # print(city_data)

    # print(neighbor_data)

    # Returns

    # Returns the point and it's respective radius as output
    # return point.coords[0], area.exterior.coords[0]

def main(argv):
        
    if len(argv) != 2:
        print("Usage: python find_radius.py lat lon")
        sys.exit(1)
    
    # Gets input from user and turns it into a shapely point
    return run_query(argv)
    
if __name__ == "__main__":

    main(sys.argv[1:])