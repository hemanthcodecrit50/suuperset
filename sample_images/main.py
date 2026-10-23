import os
from dotenv import load_dotenv
load_dotenv()
from selenium import webdriver
from selenium.webdriver.common.by import By

project_dir = os.path.dirname(os.path.abspath(__file__))
profile_path = os.path.join(project_dir, "chrome_profile")

options = webdriver.ChromeOptions()
options.add_argument(f"--user-data-dir={profile_path}")

driver = webdriver.Chrome(options=options)

USER_EMAIL = os.getenv("USER_EMAIL")
USER_PASSWORD = os.getenv("USER_PASSWORD")


try:

    driver.get("https://app.joinsuperset.com/students")
    driver.implicitly_wait(3)
    if driver.title != 'Home':

        email = driver.find_element(By.ID, '«r5»')
        email.send_keys(USER_EMAIL)

        password = driver.find_element(By.ID,"«r6»")
        password.send_keys(USER_PASSWORD)

        submit_button = driver.find_element(By.XPATH, "//button[normalize-space()='Login']")
        submit_button.click()

        print(driver.title)
    else: 
    
        print("[DEBUG] loggin in has been done")
    
    job_profile_button = driver.find_element(By.ID,'demo_step_3')
    job_profile_button.click()

    driver.implicitly_wait(3)

    status_filter = driver.find_element(By.ID, "mui-component-select-jobApplicationStatus")
    status_filter.click()

    status_bar = driver.find_element(By.ID,"«rl»")

    listy = status_bar.find_elements(By.XPATH,'.//li')
    status_bar_elements={ele.text:ele for ele in listy}
    # dict_keys(['Status', 'All', 'Applied', 'Yet To Apply', 'Not Interested', 'Withdrawn', 'Rejected', 'Offered', 'Offer Accepted', 'Offer Rejected'])
    
    yet_to_be_applied_jobs = status_bar_elements['Yet To Apply']
    


    


except Exception as e:# 4. Clean up and close the browser session
    print(e)
finally:
    driver.quit()

